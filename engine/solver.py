"""Seeded construction and local search over independently evaluated tours."""
import copy
import hashlib
import json
import math
import os
import platform
import random
import time
import uuid
from pathlib import Path

from . import __version__
from .contracts import InputError, RUN, check, validate_scenario
from .validation import objective_score, validate_partial, validate_solution


def scenario_hash(scenario):
    return hashlib.sha256(json.dumps(scenario, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def runtime_info():
    cpu = platform.processor() or platform.machine() or 'unknown'
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                cpu = line.split(':', 1)[1].strip()
                break
    except OSError:
        pass
    return dict(python=platform.python_version(), platform=platform.platform(),
                machine=platform.machine() or 'unknown', cpu_model=cpu,
                logical_cpus=os.cpu_count() or 0, device='cpu', worker_count=1,
                thread_count=1)


def proposal(s, routes):
    return dict(schema_version=1, scenario_id=s['id'], routes=[
        dict(vehicle_id=v, stop_ids=list(route)) for v, route in zip(s['vehicle_ids'], routes)])


def neighbor_moves(routes):
    """Stable order: relocation, swap, then within-route reversal."""
    for a, route in enumerate(routes):
        for i in range(len(route)):
            for b, dest in enumerate(routes):
                for j in range(len(dest) + 1):
                    if a == b and j in (i, i + 1):
                        continue
                    moved = copy.deepcopy(routes)
                    stop = moved[a].pop(i)
                    index = j - 1 if a == b and j > i else j
                    moved[b].insert(index, stop)
                    yield moved, 'relocate'
    positions = [(a, i) for a, route in enumerate(routes) for i in range(len(route))]
    for x, (a, i) in enumerate(positions):
        for b, j in positions[x + 1:]:
            moved = copy.deepcopy(routes)
            moved[a][i], moved[b][j] = moved[b][j], moved[a][i]
            yield moved, 'swap'
    for a, route in enumerate(routes):
        for i in range(len(route)):
            for j in range(i + 2, len(route) + 1):
                moved = copy.deepcopy(routes)
                moved[a][i:j] = reversed(moved[a][i:j])
                yield moved, 'reverse'


def neighbors(routes):
    """Route-only interface retained for consumers of the M1 neighborhood API."""
    for moved, _ in neighbor_moves(routes):
        yield moved


class StopSearch(Exception):
    def __init__(self, reason):
        self.reason = reason


def solve(data, *, seed=0, budget_s=1.0, max_evaluations=None, restarts=8,
          clock=time.monotonic, startup_s=0.0, preparation_s=0.0,
          observe=None, cancelled=None, selection_policy="ordered", candidate_observer=None):
    """Budget starts before scenario-specific validation/preparation.

    max_evaluations allows deterministic comparisons when wall time is ample.
    Final incumbent revalidation is mandatory and may exceed the soft budget.
    """
    started = clock() - preparation_s
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise InputError('seed must be an integer')
    if isinstance(budget_s, bool) or not isinstance(budget_s, (int, float)) or not math.isfinite(budget_s) or budget_s < 0:
        raise InputError('budget_s must be finite and nonnegative')
    if max_evaluations is not None and (type(max_evaluations) is not int or max_evaluations < 0):
        raise InputError('max_evaluations must be a nonnegative integer')
    if type(restarts) is not int or restarts < 1:
        raise InputError('restarts must be a positive integer')
    s = validate_scenario(data)
    digest = scenario_hash(s)
    runtime = runtime_info()
    rng = random.Random(seed)
    selection_rng = random.Random(seed)
    if selection_policy not in ("ordered", "shuffled"):
        raise InputError("unsupported selection policy")
    evaluations = 0
    best, best_score, first = None, math.inf, None
    search_started = clock()
    reason = 'search_exhausted'
    recording_s = 0.0
    last_metric = started

    def emit(kind, payload):
        nonlocal recording_s
        if observe is not None:
            before = clock()
            observe(kind, payload, max(0, before - started))
            recording_s += clock() - before

    emit('initial', dict(solution=None, validation=None, partial=True))

    def evaluate(routes, partial=False):
        nonlocal evaluations, last_metric
        if cancelled is not None and cancelled():
            raise StopSearch('cancelled')
        if clock() - started >= budget_s:
            raise StopSearch('budget_exhausted')
        if max_evaluations is not None and evaluations >= max_evaluations:
            raise StopSearch('evaluation_limit')
        evaluations += 1
        candidate = proposal(s, routes)
        report = (validate_partial if partial else validate_solution)(s, candidate)
        if observe is not None and clock() - last_metric >= 1:
            emit('metric', dict(evaluations=evaluations))
            last_metric = clock()
        if not partial and report["feasible"] and candidate_observer is not None:
            candidate_observer(candidate, report, clock() - started)
        return report

    def remember(routes, report):
        nonlocal best, best_score, first
        score = objective_score(s, report)
        if score < best_score:
            best, best_score = copy.deepcopy(routes), score
            if first is None:
                first = clock() - started
            emit('feasible_best', dict(solution=proposal(s, routes), validation=report, partial=False))

    try:
        for attempt in range(restarts):
            routes = [[] for _ in s['vehicle_ids']]
            order = sorted(s['stops'], key=lambda stop: (stop['window_close_s'], stop['id']))
            if attempt:
                rng.shuffle(order)
            constructed = True
            for stop in order:
                chosen, score = None, math.inf
                for a, route in enumerate(routes):
                    for i in range(len(route) + 1):
                        candidate = copy.deepcopy(routes)
                        candidate[a].insert(i, stop['id'])
                        report = evaluate(candidate, partial=True)
                        if report['feasible']:
                            value = objective_score(s, report)
                            if value < score:
                                chosen, score = candidate, value
                if chosen is None:
                    constructed = False
                    break
                routes = chosen
                if observe is not None:
                    emit('accepted_insertion', dict(solution=proposal(s, routes),
                         validation=validate_partial(s, proposal(s, routes)), partial=True,
                         move_type='insertion', stop_id=stop['id']))
            if not constructed:
                continue
            report = evaluate(routes)
            if not report['feasible']:
                raise RuntimeError('construction produced an invalid complete solution')
            remember(routes, report)
            current_score = objective_score(s, report)
            current_report = report
            while True:
                improved = False
                moves = neighbor_moves(routes)
                if selection_policy == "shuffled":
                    moves = list(moves)
                    selection_rng.shuffle(moves)
                for candidate, move_type in moves:
                    report = evaluate(candidate)
                    if report['feasible']:
                        score = objective_score(s, report)
                        if score < current_score:
                            routes, current_score = candidate, score
                            emit('accepted_move', dict(solution=proposal(s, routes),
                                 validation=report, partial=False, move_type=move_type,
                                 delta={key:report['metrics'][key]-current_report['metrics'][key]
                                        for key in ('fleet_elapsed_s','estimated_fuel_l')}))
                            current_report = report
                            remember(routes, report)
                            improved = True
                            break
                if not improved:
                    break
    except StopSearch as stop:
        reason = stop.reason
    search_s = clock() - search_started
    solution = proposal(s, best) if best is not None else None
    validation = validate_solution(s, solution) if solution is not None else None
    if validation is not None and not validation['feasible']:
        raise RuntimeError('incumbent failed final independent validation')
    result = dict(schema_version=s['schema_version'], run_id=str(uuid.uuid4()), scenario_hash=digest,
                  scenario=s, solver=dict(name='insertion-local-search', version=__version__),
                  config=dict(seed=seed, budget_s=budget_s, max_evaluations=max_evaluations,
                              restarts=restarts), runtime=runtime,
                  timing=dict(solve_s=clock() - started, search_s=search_s,
                              startup_s=startup_s, time_to_first_feasible_s=first),
                  evaluations=evaluations, job_status='cancelled' if reason == 'cancelled' else 'completed',
                  feasibility='feasible' if solution is not None else 'unknown',
                  termination_reason=reason, proof_status='not_certified',
                  solution=solution, validation=validation)
    if s['schema_version'] == 2:
        result['timing']['recording_s'] = recording_s
    from .contracts_v2 import run_schema
    check(result, run_schema(result))
    result['timing']['solve_s'] = clock() - started
    return result
