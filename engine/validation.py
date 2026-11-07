"""Independent schedule reconstruction; no search-policy imports or trusted totals."""
import math
from collections import Counter
from decimal import Decimal, localcontext

from .contracts import InputError, SOLUTION, check


def empty_metrics(available=1):
    return dict(fleet_elapsed_s=0, completion_s=0, travel_s=0, waiting_s=0,
                service_s=0, distance_m=0, estimated_fuel_l=0,
                used_vans=0, available_vans=available)


def validate_solution(scenario, solution):
    """Validate a complete proposal. Scenario must pass validate_scenario first."""
    return _account(scenario, solution, require_all=True)


def validate_partial(scenario, solution):
    """Construction-only accounting; a partial result is never a final solution."""
    return _account(scenario, solution, require_all=False)


def _account(s, proposal, require_all):
    check(proposal, SOLUTION)
    if proposal['scenario_id'] != s['id']:
        raise InputError('solution scenario_id does not match scenario')
    stops = {stop['id']: stop for stop in s['stops']}
    legs = {(leg['source'], leg['target']): leg for leg in s['legs']}
    errors, vehicles = [], []
    known_vehicles = set(s['vehicle_ids'])
    vehicle_counts = Counter(r['vehicle_id'] for r in proposal['routes'])
    visits = Counter(stop for r in proposal['routes'] for stop in r['stop_ids'])
    for vehicle, count in vehicle_counts.items():
        if vehicle not in known_vehicles:
            errors.append(f'unknown vehicle: {vehicle}')
        if count > 1:
            errors.append(f'duplicate vehicle route: {vehicle}')
    for stop, count in visits.items():
        if stop not in stops:
            errors.append(f'unknown stop: {stop}')
        if count > 1:
            errors.append(f'duplicate stop: {stop}')
    if require_all:
        errors.extend(f'missing stop: {stop}' for stop in sorted(stops.keys() - visits.keys()))
    totals = empty_metrics(len(known_vehicles))
    for route in proposal['routes']:
        vehicle = route['vehicle_id']
        if vehicle not in known_vehicles or any(i not in stops for i in route['stop_ids']):
            continue
        metrics = empty_metrics(len(known_vehicles))
        report = dict(vehicle_id=vehicle, stop_ids=list(route['stop_ids']),
                      return_s=s['start_s'], visits=[], legs=[], metrics=metrics)
        vehicles.append(report)
        if not route['stop_ids']:
            continue
        metrics['used_vans'] = 1
        now, mass, volume = s['start_s'], 0, 0
        exact_mass, exact_volume = Decimal(0), Decimal(0)
        previous = s['depot_id']
        blocked = False
        for target in route['stop_ids'] + [s['depot_id']]:
            leg = legs.get((previous, target))
            if leg is None or not leg['reachable']:
                errors.append(f'{vehicle}: unreachable leg {previous}->{target}')
                blocked = True
                break
            distance = sum(seg['distance_m'] for seg in leg['segments'])
            fuel = 0
            for segment in leg['segments']:
                coef = s['fuel_model']['categories'][segment['driving_category']]
                fuel += segment['distance_m'] / 1000 * (
                    coef['base_l_per_km'] + coef['load_l_per_km_kg'] * mass)
            report['legs'].append(dict(source=previous, target=target,
                                       travel_s=leg['travel_s'], distance_m=distance,
                                       load_kg=mass, load_m3=volume, estimated_fuel_l=fuel))
            metrics['travel_s'] += leg['travel_s']
            metrics['distance_m'] += distance
            metrics['estimated_fuel_l'] += fuel
            now += leg['travel_s']
            if target != s['depot_id']:
                stop = stops[target]
                arrival = now
                start = max(arrival, stop['window_open_s'])
                if start > stop['window_close_s']:
                    errors.append(f'{vehicle}: late service start at {target}')
                service = s['service']
                duration = (service['constant_s'] if service['mode'] == 'constant'
                            else service['fixed_s'] + service['per_package_s'] * stop['packages'])
                metrics['waiting_s'] += start - arrival
                metrics['service_s'] += duration
                if s['fuel_model']['idle_policy'] == 'waiting_and_service':
                    metrics['estimated_fuel_l'] += (
                        start - arrival + duration) * s['fuel_model']['idle_l_per_s']
                now = start + duration
                # Compare decimal input quantities exactly: 0.1 + 0.2 must fit 0.3.
                # 700 digits covers the range of finite binary64 input values.
                with localcontext() as context:
                    context.prec = 700
                    exact_mass += Decimal(str(stop['mass_kg']))
                    exact_volume += Decimal(str(stop['volume_m3']))
                mass, volume = float(exact_mass), float(exact_volume)
                if (exact_mass > Decimal(str(s['capacity']['mass_kg']))
                        or exact_volume > Decimal(str(s['capacity']['volume_m3']))):
                    errors.append(f'{vehicle}: capacity exceeded after {target}')
                report['visits'].append(dict(stop_id=target, arrival_s=arrival,
                                             service_start_s=start, departure_s=now,
                                             load_kg=mass, load_m3=volume))
            previous = target
        report['return_s'] = now
        metrics['fleet_elapsed_s'] = now - s['start_s']
        metrics['completion_s'] = now - s['start_s']
        if not blocked and s['duration_cap_s'] is not None and now - s['start_s'] > s['duration_cap_s']:
            errors.append(f'{vehicle}: duration cap exceeded on depot return')
        for key in totals:
            if key == 'completion_s':
                totals[key] = max(totals[key], metrics[key])
            elif key != 'available_vans':
                totals[key] += metrics[key]
    for value in totals.values():
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise InputError('accounting overflow: reduce input magnitudes')
    return dict(feasible=not errors, violations=errors, metrics=totals,
                vehicles=vehicles, fuel_label=f"{s['fuel_model']['label']} (uncalibrated)")


def objective_score(scenario, report):
    config, metrics = scenario['objective'], report['metrics']
    score = (config['time_weight'] * (metrics['fleet_elapsed_s'] / config['time_scale_s'])
             + config['fuel_weight'] * (metrics['estimated_fuel_l'] / config['fuel_scale_l']))
    if not math.isfinite(score):
        raise InputError('objective overflow: reduce weights or increase scales')
    return score
