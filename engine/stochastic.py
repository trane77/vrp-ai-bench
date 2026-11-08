"""Independent Monte Carlo stochastic robustness evaluator (M6).

Evaluates a deterministic feasible solution under seeded synthetic perturbations
of road travel times and stop service durations without altering the deterministic
validator.
"""
import math
import random

from .contracts import InputError, validate_scenario
from .validation import select_leg_path, validate_solution


def _quantile(sorted_values, q):
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    weight = pos - lo
    return float(sorted_values[lo] * (1.0 - weight) + sorted_values[hi] * weight)


def _summary(values):
    if not values:
        return dict(mean=0.0, p50=0.0, p95=0.0, max=0.0)
    ordered = sorted(float(v) for v in values)
    return dict(
        mean=round(sum(ordered) / len(ordered), 4),
        p50=round(_quantile(ordered, 0.50), 4),
        p95=round(_quantile(ordered, 0.95), 4),
        max=round(ordered[-1], 4),
    )


def _multiplier(rng, cv):
    if cv <= 0.0:
        return 1.0
    sigma2 = math.log(1.0 + cv * cv)
    sigma = math.sqrt(sigma2)
    mu = -0.5 * sigma2
    factor = math.exp(rng.gauss(mu, sigma))
    return min(4.0, max(0.5, factor))


def evaluate_stochastic(scenario, solution, *, seed=42, samples=100, travel_cv=0.15, service_cv=0.20):
    """Simulate a validated feasible solution across `samples` Monte Carlo realizations."""
    if type(seed) is not int:
        raise InputError('seed must be an integer')
    if type(samples) is not int or not (1 <= samples <= 1000):
        raise InputError('samples must be an integer between 1 and 1000')
    if not isinstance(travel_cv, (int, float)) or isinstance(travel_cv, bool) or not math.isfinite(travel_cv) or not (0.0 <= travel_cv <= 2.0):
        raise InputError('travel_cv must be a finite number in [0, 2]')
    if not isinstance(service_cv, (int, float)) or isinstance(service_cv, bool) or not math.isfinite(service_cv) or not (0.0 <= service_cv <= 2.0):
        raise InputError('service_cv must be a finite number in [0, 2]')

    s = validate_scenario(scenario)
    baseline = validate_solution(s, solution)
    if not baseline['feasible']:
        raise InputError('stochastic evaluation requires a deterministically feasible baseline solution')

    stops = {stop['id']: stop for stop in s['stops']}
    legs = {(leg['source'], leg['target']): leg for leg in s['legs']}
    nominal_visits = {
        v['stop_id']: v
        for r in baseline['vehicles']
        for v in r['visits']
    }

    rng = random.Random(seed)
    feasible_count = 0
    window_ok_count = 0
    cap_ok_count = 0

    fleet_elapsed_samples = []
    completion_samples = []
    waiting_samples = []
    lateness_samples = []
    fuel_samples = []

    stop_late_counts = {stop_id: 0 for stop_id in stops}
    stop_lateness = {stop_id: [] for stop_id in stops}
    stop_arrivals = {stop_id: [] for stop_id in stops}

    vehicle_cap_violations = {v: 0 for v in s['vehicle_ids']}
    vehicle_durations = {v: [] for v in s['vehicle_ids']}

    for _ in range(samples):
        all_windows_ok = True
        all_caps_ok = True
        sample_fleet_elapsed = 0.0
        sample_completion = 0.0
        sample_waiting = 0.0
        sample_lateness = 0.0
        sample_fuel = 0.0

        for route in solution['routes']:
            vid = route['vehicle_id']
            stop_ids = route['stop_ids']
            if not stop_ids:
                vehicle_durations[vid].append(0.0)
                continue

            now = float(s['start_s'])
            mass = sum(float(stops[sid].get('delivery_mass_kg', 0.0)) for sid in stop_ids)
            work_since_break_s = 0.0
            brk = s.get('driver_break')
            brk_enabled = bool(brk and brk.get('enabled'))
            explicit_pids = route.get('path_ids')
            previous = s['depot_id']

            for leg_idx, target in enumerate(stop_ids + [s['depot_id']]):
                leg = legs[previous, target]
                win_close = stops[target]['window_close_s'] if target != s['depot_id'] else None
                exp_pid = (explicit_pids[leg_idx]
                           if explicit_pids is not None and leg_idx < len(explicit_pids)
                           else None)
                _, base_travel_s, segments, _ = select_leg_path(
                    s, leg, mass, now, window_close_s=win_close, explicit_path_id=exp_pid)
                t_factor = _multiplier(rng, float(travel_cv))
                travel_s = base_travel_s * t_factor

                fuel = 0.0
                for segment in segments:
                    coef = s['fuel_model']['categories'][segment['driving_category']]
                    fuel += segment['distance_m'] / 1000.0 * (
                        coef['base_l_per_km'] + coef['load_l_per_km_kg'] * mass
                    )
                sample_fuel += fuel
                now += travel_s
                work_since_break_s += travel_s

                if target != s['depot_id']:
                    stop = stops[target]
                    arrival = now
                    stop_arrivals[target].append(arrival)
                    start = max(arrival, float(stop['window_open_s']))
                    late_s = max(0.0, start - float(stop['window_close_s']))
                    if late_s > 1e-9:
                        all_windows_ok = False
                        stop_late_counts[target] += 1
                    else:
                        late_s = 0.0
                    stop_lateness[target].append(late_s)
                    sample_lateness += late_s

                    service_cfg = s['service']
                    nominal_service = (
                        service_cfg['constant_s']
                        if service_cfg['mode'] == 'constant'
                        else service_cfg['fixed_s'] + service_cfg['per_package_s'] * stop['packages']
                    )
                    s_factor = _multiplier(rng, float(service_cv))
                    service_s = nominal_service * s_factor
                    wait_s = start - arrival
                    sample_waiting += wait_s

                    if s['fuel_model']['idle_policy'] == 'waiting_and_service':
                        sample_fuel += (wait_s + service_s) * s['fuel_model']['idle_l_per_s']

                    now = start + service_s
                    work_since_break_s += service_s
                    if brk_enabled and work_since_break_s >= float(brk['threshold_s']):
                        brk_s = float(brk['duration_s'])
                        now += brk_s
                        sample_waiting += brk_s
                        work_since_break_s = 0.0
                    mass = mass - float(stop.get('delivery_mass_kg', 0.0)) + float(stop['mass_kg'])

                previous = target

            duration_s = now - float(s['start_s'])
            vehicle_durations[vid].append(duration_s)
            sample_fleet_elapsed += duration_s
            sample_completion = max(sample_completion, duration_s)

            if s['duration_cap_s'] is not None and duration_s > float(s['duration_cap_s']) + 1e-9:
                all_caps_ok = False
                vehicle_cap_violations[vid] += 1

        if all_windows_ok:
            window_ok_count += 1
        if all_caps_ok:
            cap_ok_count += 1
        if all_windows_ok and all_caps_ok:
            feasible_count += 1

        fleet_elapsed_samples.append(sample_fleet_elapsed)
        completion_samples.append(sample_completion)
        waiting_samples.append(sample_waiting)
        lateness_samples.append(sample_lateness)
        fuel_samples.append(sample_fuel)

    stop_risks = []
    for stop in s['stops']:
        sid = stop['id']
        visit = nominal_visits[sid]
        nominal_slack = float(stop['window_close_s'] - visit['service_start_s'])
        lateness_summary = _summary(stop_lateness[sid])
        arrival_summary = _summary(stop_arrivals[sid])
        stop_risks.append(dict(
            stop_id=sid,
            window_open_s=stop['window_open_s'],
            window_close_s=stop['window_close_s'],
            nominal_arrival_s=visit['arrival_s'],
            nominal_service_start_s=visit['service_start_s'],
            nominal_slack_s=round(nominal_slack, 2),
            late_rate=round(stop_late_counts[sid] / samples, 4),
            mean_lateness_s=lateness_summary['mean'],
            p95_lateness_s=lateness_summary['p95'],
            p95_arrival_s=arrival_summary['p95'],
        ))
    stop_risks.sort(key=lambda item: (-item['late_rate'], item['nominal_slack_s'], item['stop_id']))

    vehicle_risks = []
    for v_report in baseline['vehicles']:
        vid = v_report['vehicle_id']
        dur_summary = _summary(vehicle_durations[vid])
        vehicle_risks.append(dict(
            vehicle_id=vid,
            stops_count=len(v_report['stop_ids']),
            nominal_duration_s=v_report['metrics']['fleet_elapsed_s'],
            duration_cap_s=s['duration_cap_s'],
            cap_violation_rate=round(vehicle_cap_violations[vid] / samples, 4),
            mean_duration_s=dur_summary['mean'],
            p95_duration_s=dur_summary['p95'],
        ))

    return dict(
        schema_version=1,
        kind='stochastic_robustness_report',
        scenario_id=s['id'],
        model=dict(
            distribution='clamped_lognormal_mean_1',
            clamp_bounds=[0.5, 4.0],
            seed=seed,
            samples=samples,
            travel_cv=float(travel_cv),
            service_cv=float(service_cv),
            calibration='synthetic_perturbation_not_live_traffic',
            label='Simulazione Monte Carlo sintetica; non calibrata su telemetria di traffico reale',
        ),
        baseline_metrics=baseline['metrics'],
        feasible_rate=round(feasible_count / samples, 4),
        window_compliance_rate=round(window_ok_count / samples, 4),
        duration_cap_compliance_rate=round(cap_ok_count / samples, 4),
        metrics=dict(
            fleet_elapsed_s=_summary(fleet_elapsed_samples),
            completion_s=_summary(completion_samples),
            waiting_s=_summary(waiting_samples),
            total_lateness_s=_summary(lateness_samples),
            estimated_fuel_l=_summary(fuel_samples),
        ),
        stop_risks=stop_risks,
        vehicle_risks=vehicle_risks,
    )
