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


def _leg_fuel(s, segments, mass):
    fuel = 0
    for segment in segments:
        coef = s['fuel_model']['categories'][segment['driving_category']]
        fuel += segment['distance_m'] / 1000 * (
            coef['base_l_per_km'] + coef['load_l_per_km_kg'] * mass)
    return fuel


def select_leg_path(s, leg, mass, now, window_close_s=None, explicit_path_id=None):
    """Select the active road path for a leg (explicit path_id, objective-aware alternative, or default)."""
    alts = leg.get('alternatives') or []
    if explicit_path_id is not None:
        for alt in alts:
            if alt['id'] == explicit_path_id:
                return alt['id'], alt['travel_s'], alt['segments'], None
        if explicit_path_id == 'default':
            return 'default', leg['travel_s'], leg['segments'], None
        return (explicit_path_id, leg['travel_s'], leg['segments'],
                f"unknown path_id {explicit_path_id} on leg {leg['source']}->{leg['target']}")
    if s.get('path_optimization'):
        if not alts:
            return 'default', leg['travel_s'], leg['segments'], None
        obj_cfg = s['objective']
        tw, fw = obj_cfg['time_weight'], obj_cfg['fuel_weight']
        ts, fs = obj_cfg['time_scale_s'], obj_cfg['fuel_scale_l']
        scored = []
        for alt in alts:
            t_s = alt['travel_s']
            f_l = _leg_fuel(s, alt['segments'], mass)
            on_time = (window_close_s is None) or (now + t_s <= window_close_s)
            score = tw * (t_s / ts) + fw * (f_l / fs)
            scored.append((on_time, score, t_s, alt['id'], alt))
        on_time_alts = [item for item in scored if item[0]]
        if on_time_alts:
            best = min(on_time_alts, key=lambda item: (item[1], item[2], item[3]))[4]
        else:
            best = min(scored, key=lambda item: (item[2], item[1], item[3]))[4]
        return best['id'], best['travel_s'], best['segments'], None
    return None, leg['travel_s'], leg['segments'], None


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
    brk = s.get('driver_break')
    brk_enabled = bool(brk and brk.get('enabled'))
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
        v_cap = (s.get('vehicle_capacities') or {}).get(vehicle, s['capacity'])
        with localcontext() as context:
            context.prec = 700
            exact_mass = sum((Decimal(str(stops[sid].get('delivery_mass_kg', 0)))
                              for sid in route['stop_ids']), Decimal(0))
            exact_volume = sum((Decimal(str(stops[sid].get('delivery_volume_m3', 0)))
                                for sid in route['stop_ids']), Decimal(0))
        mass = float(exact_mass) if exact_mass else 0
        volume = float(exact_volume) if exact_volume else 0
        if (exact_mass > Decimal(str(v_cap['mass_kg']))
                or exact_volume > Decimal(str(v_cap['volume_m3']))):
            errors.append(f'{vehicle}: capacity exceeded at depot departure')
        explicit_pids = route.get('path_ids')
        if explicit_pids is not None and not s.get('path_optimization'):
            errors.append(f'{vehicle}: path_ids are not allowed under the fixed_directed_paths policy')
        if explicit_pids is not None and len(explicit_pids) != len(route['stop_ids']) + 1:
            errors.append(f'{vehicle}: path_ids length must match legs count')
        now = s['start_s']
        work_since_break_s = 0
        previous = s['depot_id']
        blocked = False
        for leg_idx, target in enumerate(route['stop_ids'] + [s['depot_id']]):
            leg = legs.get((previous, target))
            if leg is None or not leg['reachable']:
                errors.append(f'{vehicle}: unreachable leg {previous}->{target}')
                blocked = True
                break
            win_close = stops[target]['window_close_s'] if target != s['depot_id'] else None
            exp_pid = (explicit_pids[leg_idx]
                       if explicit_pids is not None and leg_idx < len(explicit_pids)
                       else None)
            path_id, travel_s, segments, path_err = select_leg_path(
                s, leg, mass, now, window_close_s=win_close, explicit_path_id=exp_pid)
            if path_err:
                errors.append(f'{vehicle}: {path_err}')
            distance = sum(seg['distance_m'] for seg in segments)
            fuel = _leg_fuel(s, segments, mass)
            leg_rep = dict(source=previous, target=target,
                           travel_s=travel_s, distance_m=distance,
                           load_kg=mass, load_m3=volume, estimated_fuel_l=fuel)
            if path_id is not None:
                leg_rep['path_id'] = path_id
            report['legs'].append(leg_rep)
            metrics['travel_s'] += travel_s
            metrics['distance_m'] += distance
            metrics['estimated_fuel_l'] += fuel
            now += travel_s
            work_since_break_s += travel_s
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
                work_since_break_s += duration
                break_s = 0
                if brk_enabled and work_since_break_s >= brk['threshold_s']:
                    break_s = int(brk['duration_s'])
                    now += break_s
                    metrics['waiting_s'] += break_s
                    work_since_break_s = 0
                # Compare decimal input quantities exactly: 0.1 + 0.2 must fit 0.3.
                # 700 digits covers the range of finite binary64 input values.
                with localcontext() as context:
                    context.prec = 700
                    exact_mass = (exact_mass
                                  - Decimal(str(stop.get('delivery_mass_kg', 0)))
                                  + Decimal(str(stop['mass_kg'])))
                    exact_volume = (exact_volume
                                    - Decimal(str(stop.get('delivery_volume_m3', 0)))
                                    + Decimal(str(stop['volume_m3'])))
                mass, volume = float(exact_mass), float(exact_volume)
                if (exact_mass > Decimal(str(v_cap['mass_kg']))
                        or exact_volume > Decimal(str(v_cap['volume_m3']))):
                    errors.append(f'{vehicle}: capacity exceeded after {target}')
                visit_rep = dict(stop_id=target, arrival_s=arrival,
                                 service_start_s=start, departure_s=now,
                                 load_kg=mass, load_m3=volume)
                if brk_enabled:
                    visit_rep['break_s'] = break_s
                report['visits'].append(visit_rep)
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
    cal_status = s['fuel_model'].get('calibration', 'uncalibrated')
    return dict(feasible=not errors, violations=errors, metrics=totals,
                vehicles=vehicles, fuel_label=f"{s['fuel_model']['label']} ({cal_status})")


def objective_score(scenario, report):
    config, metrics = scenario['objective'], report['metrics']
    score = (config['time_weight'] * (metrics['fleet_elapsed_s'] / config['time_scale_s'])
             + config['fuel_weight'] * (metrics['estimated_fuel_l'] / config['fuel_scale_l']))
    if not math.isfinite(score):
        raise InputError('objective overflow: reduce weights or increase scales')
    return score
