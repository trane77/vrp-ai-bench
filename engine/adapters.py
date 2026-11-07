"""Common solver request and independently validated optional routing adapter."""
from dataclasses import dataclass
from decimal import Decimal
import time

from .contracts import InputError, validate_scenario
from .solver import proposal, solve
from .validation import validate_solution

LIMIT = 2**60


def exact_integers(values):
    decimals = [Decimal(str(v)) for v in values]
    digits = max(0, max(-v.as_tuple().exponent for v in decimals))
    if digits > 18:
        raise InputError('adapter decimal scale exceeds 18 digits')
    scale = 10**digits
    # Decimal multiplication can round under the default context; integer ratios cannot.
    integers = []
    for value in decimals:
        numerator, denominator = value.as_integer_ratio()
        numerator *= scale
        if numerator % denominator or abs(numerator // denominator) >= LIMIT:
            raise InputError('adapter quantity is not exactly representable')
        integers.append(numerator // denominator)
    if sum(integers) >= LIMIT:
        raise InputError('adapter aggregate quantity exceeds numeric limit')
    return scale, integers


@dataclass
class SolverRequest:
    scenario: dict
    method: str
    budget_s: float
    seed: int | None = None
    observe: object = None
    cancelled: object = None
    candidate_observer: object = None
    preparation_s: float = 0
    checkpoint: str | None = None


def run_solver(request):
    if request.method in ('ordered', 'shuffled', 'learned'):
        return solve(request.scenario, seed=request.seed, budget_s=request.budget_s,
                     selection_policy=request.method, observe=request.observe,
                     cancelled=request.cancelled, candidate_observer=request.candidate_observer,
                     preparation_s=request.preparation_s, checkpoint=request.checkpoint)
    if request.method != 'ortools':
        raise InputError('unknown solver method')
    return ortools_solve(request)


def ortools_solve(request):
    started = time.monotonic() - request.preparation_s
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    import ortools
    s = validate_scenario(request.scenario)
    if s['objective']['fuel_weight'] != 0:
        raise InputError('OR-Tools supports fleet elapsed time only')
    ids = [s['depot_id']] + [v['id'] for v in s['stops']]
    stops = [None] + s['stops']
    service = s['service']
    durations = [0] + [(service['constant_s'] if service['mode'] == 'constant' else
                       service['fixed_s'] + service['per_package_s'] * v['packages']) for v in s['stops']]
    legs = {(v['source'], v['target']): v for v in s['legs'] if v['reachable']}
    horizon = max([s['start_s']] + [v['window_close_s'] for v in s['stops']]) + sum(durations) + sum(v['travel_s'] for v in legs.values()) + 1
    if horizon * len(s['vehicle_ids']) >= LIMIT:
        raise InputError('adapter time horizon exceeds numeric limit')
    manager = pywrapcp.RoutingIndexManager(len(ids), len(s['vehicle_ids']), 0)
    routing = pywrapcp.RoutingModel(manager)
    def transit(a, b):
        i, j = manager.IndexToNode(a), manager.IndexToNode(b)
        if i == j == 0: return 0
        leg = legs.get((ids[i], ids[j]))
        return durations[i] + (leg['travel_s'] if leg else 0)
    callback = routing.RegisterTransitCallback(transit)
    routing.AddDimension(callback, horizon, horizon, False, 'Time')
    dimension = routing.GetDimensionOrDie('Time')
    dimension.SetSpanCostCoefficientForAllVehicles(1)
    for vehicle in range(len(s['vehicle_ids'])):
        dimension.CumulVar(routing.Start(vehicle)).SetValue(s['start_s'])
        routing.SetVehicleUsedWhenEmpty(True, vehicle)
        if s['duration_cap_s'] is not None:
            dimension.SetSpanUpperBoundForVehicle(s['duration_cap_s'], vehicle)
    for node, stop in enumerate(stops[1:], 1):
        dimension.CumulVar(manager.NodeToIndex(node)).SetRange(stop['window_open_s'], stop['window_close_s'])
    for a in range(routing.Size()):
        for b in range(manager.GetNumberOfIndices()):
            i, j = manager.IndexToNode(a), manager.IndexToNode(b)
            if i != j and (ids[i], ids[j]) not in legs:
                routing.NextVar(a).RemoveValue(b)
    scales = {}
    for key in ('mass_kg', 'volume_m3'):
        scale, values = exact_integers([s['capacity'][key]] + [v[key] for v in s['stops']])
        scales[key] = scale
        demands = [0] + values[1:]
        cb = routing.RegisterUnaryTransitCallback(lambda index, demands=demands: demands[manager.IndexToNode(index)])
        routing.AddDimensionWithVehicleCapacity(cb, 0, [values[0]] * len(s['vehicle_ids']), True, key)
    best, validation, best_value = None, None, float('inf')
    errors = []
    def accept(candidate):
        nonlocal best, validation, best_value
        report = validate_solution(s, candidate)
        elapsed = time.monotonic() - started
        if not report['feasible']:
            raise RuntimeError('OR-Tools solution failed independent validation')
        if request.candidate_observer:
            request.candidate_observer(candidate, report, elapsed)
        value = report['metrics']['fleet_elapsed_s']
        if value < best_value:
            best, validation, best_value = candidate, report, value
            if request.observe:
                request.observe('feasible_best', dict(solution=candidate, validation=report, partial=False), elapsed)
    def extract(value):
        routes = []
        for vehicle in range(len(s['vehicle_ids'])):
            index = value(routing.NextVar(routing.Start(vehicle)))
            route = []
            while not routing.IsEnd(index):
                route.append(ids[manager.IndexToNode(index)])
                index = value(routing.NextVar(index))
            routes.append(route)
        return proposal(s, routes)
    def callback_solution():
        try:
            accept(extract(lambda var: var.Value()))
            if request.cancelled and request.cancelled(): routing.solver().FinishCurrentSearch()
        except Exception as exc:
            errors.append(exc)
            routing.solver().FinishCurrentSearch()
    routing.AddAtSolutionCallback(callback_solution)
    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    remaining = request.budget_s - (time.monotonic() - started)
    assignment = None
    if remaining > 0:
        parameters.time_limit.FromMilliseconds(max(1, int(remaining * 1000)))
        search_started = time.monotonic()
        assignment = routing.SolveWithParameters(parameters)
    if errors: raise errors[0]
    if assignment: accept(extract(assignment.Value))
    if best is not None: validation = validate_solution(s, best)
    return dict(solution=best, validation=validation, feasibility='feasible' if best else 'unknown',
                termination_reason='cancelled' if request.cancelled and request.cancelled() else
                ('budget_exhausted' if remaining <= 0 or time.monotonic()-search_started >= max(0,remaining-.002) else 'search_exhausted'),
                proof_status='not_certified', timing=dict(solve_s=time.monotonic()-started),
                native=dict(version=ortools.__version__, parameters=str(parameters), decimal_scales=scales,
                            random_seed_control=False, routing_status=routing.status(), threads=1))
