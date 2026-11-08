"""Offline traffic microsimulation and calibrated fuel validation (M8).

Replays feasible VRP schedules over frozen road segments under 1-second longitudinal
vehicle dynamics (acceleration, congestion-modulated cruising, braking, and signalized
intersection queues) and a tractive-power diesel consumption reference model.
Calibrates the parametric `linear-load-v1` fuel model via ordinary least squares
and strictly separates uncalibrated parametric estimates from simulation-calibrated
fuel metrics. Also includes an XML parser for external Eclipse SUMO `<emission-export>`
traces.
"""
import copy
import math
import random
import xml.etree.ElementTree as ET

from .contracts import InputError, validate_scenario
from .validation import select_leg_path, validate_solution


# Physical constants for a light commercial delivery van (e.g., Euro 6 diesel van)
DEFAULT_CURB_MASS_KG = 2200.0
GRAVITY_M_S2 = 9.81
AIR_DENSITY_KG_M3 = 1.225
ROLLING_RESISTANCE_CR = 0.010
CD_FRONTAL_AREA_M2 = 0.35 * 2.80  # Cd=0.35, A=2.8 m^2 -> 0.98 m^2
ROTATIONAL_INERTIA_FACTOR = 1.04
DRIVETRAIN_EFFICIENCY = 0.88
ENGINE_THERMAL_EFFICIENCY = 0.38
DIESEL_LHV_J_PER_L = 35_800_000.0  # 35.8 MJ/L
IDLE_FUEL_L_PER_S = 0.00028  # ~1.0 L/h at curb idle
CO2_KG_PER_L_DIESEL = 2.64


def _tractive_fuel_l_per_s(speed_m_s, accel_m_s2, total_mass_kg):
    """Instantaneous diesel rate (L/s) from longitudinal tractive power at wheels."""
    if speed_m_s <= 1e-6 and accel_m_s2 <= 1e-6:
        return IDLE_FUEL_L_PER_S
    p_roll = ROLLING_RESISTANCE_CR * total_mass_kg * GRAVITY_M_S2 * speed_m_s
    p_aero = 0.5 * AIR_DENSITY_KG_M3 * CD_FRONTAL_AREA_M2 * (speed_m_s ** 3)
    p_acc = ROTATIONAL_INERTIA_FACTOR * total_mass_kg * accel_m_s2 * speed_m_s
    p_wheel = max(0.0, p_roll + p_aero + p_acc)
    eta = DRIVETRAIN_EFFICIENCY * ENGINE_THERMAL_EFFICIENCY
    return IDLE_FUEL_L_PER_S + p_wheel / (eta * DIESEL_LHV_J_PER_L)


def simulate_segment_microsim(
    distance_m,
    driving_category,
    mass_kg,
    *,
    rng,
    congestion_factor=1.15,
    signal_delay_scale=1.0,
    curb_mass_kg=DEFAULT_CURB_MASS_KG,
):
    """Simulate a single road segment at 1s resolution with signal queue & longitudinal dynamics."""
    dist = float(distance_m)
    if dist <= 0.0:
        return dict(travel_s=0.0, signal_delay_s=0.0, fuel_l=0.0, speed_kmh=0.0)

    # Nominal free-flow speed by category
    if driving_category == "slow":
        base_speed_m_s = 6.5  # ~23.4 km/h
        stop_prob_per_km = 1.6
        max_red_s = 28.0
    elif driving_category == "urban":
        base_speed_m_s = 11.5  # ~41.4 km/h
        stop_prob_per_km = 0.9
        max_red_s = 22.0
    else:  # "fast"
        base_speed_m_s = 19.5  # ~70.2 km/h
        stop_prob_per_km = 0.15
        max_red_s = 12.0

    # Congestion wave reduces target cruising speed and increases stop-and-go oscillation
    wave = rng.uniform(0.92, 1.08)
    target_speed_m_s = max(2.5, (base_speed_m_s / max(0.8, float(congestion_factor))) * wave)
    total_mass = float(curb_mass_kg) + max(0.0, float(mass_kg))

    accel_rate = 1.4  # m/s^2
    decel_rate = 1.8  # m/s^2

    # Determine number of signalized / congested stop events along this segment
    expected_stops = (dist / 1000.0) * stop_prob_per_km * max(0.0, float(signal_delay_scale))
    n_stops = int(math.floor(expected_stops))
    if rng.random() < (expected_stops - n_stops):
        n_stops += 1

    # Sub-divide segment into sub-hops between stop events
    hops = max(1, n_stops + 1)
    hop_dist = dist / hops
    total_time_s = 0.0
    signal_delay_s = 0.0
    total_fuel_l = 0.0

    for h in range(hops):
        # Accelerate from 0 (or cruising entry) to target_speed_m_s, cruise, and optionally brake for signal
        stops_at_end = h < n_stops
        v_peak = min(target_speed_m_s, math.sqrt(max(4.0, 2.0 * hop_dist / (1.0 / accel_rate + (1.0 / decel_rate if stops_at_end else 0.2)))))
        t_acc = v_peak / accel_rate if (h == 0 or stops_at_end) else 0.3 * (v_peak / accel_rate)
        d_acc = 0.5 * accel_rate * (t_acc ** 2)
        t_dec = (v_peak / decel_rate) if stops_at_end else 0.2 * (v_peak / decel_rate)
        d_dec = 0.5 * decel_rate * (t_dec ** 2)
        d_cruise = max(0.0, hop_dist - d_acc - d_dec)
        t_cruise = d_cruise / max(1.0, v_peak)

        # Integrate fuel over acceleration, cruise (with mild speed ripple), and braking
        steps_acc = max(1, int(math.ceil(t_acc)))
        dt_acc = t_acc / steps_acc
        for step in range(steps_acc):
            v_mid = accel_rate * (step + 0.5) * dt_acc
            total_fuel_l += _tractive_fuel_l_per_s(v_mid, accel_rate, total_mass) * dt_acc

        if t_cruise > 0.0:
            # Mild traffic ripple (+/- 0.25 m/s^2 for 30% of cruise time under congestion)
            ripple_frac = min(0.6, 0.25 * float(congestion_factor))
            f_steady = _tractive_fuel_l_per_s(v_peak, 0.0, total_mass)
            f_ripple = _tractive_fuel_l_per_s(v_peak, 0.35, total_mass)
            total_fuel_l += ((1.0 - ripple_frac) * f_steady + ripple_frac * f_ripple) * t_cruise

        if t_dec > 0.0:
            total_fuel_l += IDLE_FUEL_L_PER_S * t_dec

        total_time_s += t_acc + t_cruise + t_dec

        if stops_at_end and signal_delay_scale > 0.0:
            red_wait_s = rng.uniform(4.0, max_red_s) * float(signal_delay_scale)
            signal_delay_s += red_wait_s
            total_time_s += red_wait_s
            total_fuel_l += IDLE_FUEL_L_PER_S * red_wait_s

    avg_speed_kmh = (dist / max(1e-3, total_time_s)) * 3.6
    return dict(
        travel_s=total_time_s,
        signal_delay_s=signal_delay_s,
        fuel_l=total_fuel_l,
        speed_kmh=avg_speed_kmh,
    )


def simulate_leg_microsim(
    leg,
    segments,
    nominal_travel_s,
    mass_kg,
    *,
    rng,
    congestion_factor=1.15,
    signal_delay_scale=1.0,
    curb_mass_kg=DEFAULT_CURB_MASS_KG,
):
    """Simulate a directed road leg across all its segments."""
    total_travel_s = 0.0
    total_signal_s = 0.0
    total_fuel_l = 0.0
    total_dist_m = 0.0
    for seg in segments:
        d_m = float(seg["distance_m"])
        total_dist_m += d_m
        res = simulate_segment_microsim(
            d_m,
            seg["driving_category"],
            mass_kg,
            rng=rng,
            congestion_factor=congestion_factor,
            signal_delay_scale=signal_delay_scale,
            curb_mass_kg=curb_mass_kg,
        )
        total_travel_s += res["travel_s"]
        total_signal_s += res["signal_delay_s"]
        total_fuel_l += res["fuel_l"]
    return dict(
        nominal_travel_s=float(nominal_travel_s),
        microsim_travel_s=total_travel_s,
        signal_delay_s=total_signal_s,
        distance_m=total_dist_m,
        fuel_l=total_fuel_l,
        co2_kg=total_fuel_l * CO2_KG_PER_L_DIESEL,
    )


def _ols_linear_fit(xs, ys):
    """Fit y = a + b * x via ordinary least squares; return (a, b, rmse, mae, r2)."""
    n = len(xs)
    if n == 0:
        return 0.1, 0.0001, 0.0, 0.0, 1.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    s_xx = sum((x - mean_x) ** 2 for x in xs)
    s_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = (s_xy / s_xx) if s_xx > 1e-12 else 0.00005
    slope = max(1e-6, slope)
    intercept = max(0.01, mean_y - slope * mean_x)
    preds = [intercept + slope * x for x in xs]
    residuals = [y - p for y, p in zip(ys, preds)]
    mse = sum(r * r for r in residuals) / n
    rmse = math.sqrt(mse)
    mae = sum(abs(r) for r in residuals) / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r2 = max(0.0, 1.0 - (sum(r * r for r in residuals) / ss_tot)) if ss_tot > 1e-12 else 1.0
    return intercept, slope, rmse, mae, r2


def calibrate_fuel_model(
    scenario,
    *,
    seed=42,
    congestion_factor=1.15,
    signal_delay_scale=1.0,
    curb_mass_kg=DEFAULT_CURB_MASS_KG,
):
    """Calibrate `linear-load-v1` coefficients against longitudinal microsimulated consumption curves."""
    s = validate_scenario(scenario)
    cap_mass = float(s["capacity"]["mass_kg"])
    mass_grid = [round(cap_mass * frac, 3) for frac in (0.0, 0.25, 0.5, 0.75, 1.0)]
    categories = sorted(s["fuel_model"]["categories"].keys())

    # Collect representative segments per category from the scenario legs (or synthetic 1 km reference if absent)
    cat_distances = {c: [] for c in categories}
    for leg in s["legs"]:
        if not leg["reachable"]:
            continue
        for seg in leg["segments"]:
            c = seg["driving_category"]
            if c in cat_distances and seg["distance_m"] >= 50.0 and len(cat_distances[c]) < 24:
                cat_distances[c].append(float(seg["distance_m"]))
    for c in categories:
        if not cat_distances[c]:
            cat_distances[c] = [500.0, 1000.0, 1500.0]

    rng = random.Random(seed)
    calibrated_cats = {}
    category_diagnostics = {}
    all_speeds = []
    all_true_l = []
    all_pred_l = []

    for c in categories:
        xs = []
        ys = []
        raw_pairs = []
        for d_m in cat_distances[c]:
            for m_kg in mass_grid:
                sim = simulate_segment_microsim(
                    d_m,
                    c,
                    m_kg,
                    rng=rng,
                    congestion_factor=congestion_factor,
                    signal_delay_scale=signal_delay_scale,
                    curb_mass_kg=curb_mass_kg,
                )
                l_per_km = sim["fuel_l"] / (d_m / 1000.0)
                xs.append(m_kg)
                ys.append(l_per_km)
                raw_pairs.append((d_m, m_kg, sim["fuel_l"]))
                if sim["speed_kmh"] > 0:
                    all_speeds.append(sim["speed_kmh"])

        base_l_km, load_l_km_kg, rmse_rate, mae_rate, r2 = _ols_linear_fit(xs, ys)
        base_rounded = round(base_l_km, 6)
        load_rounded = max(1e-6, round(load_l_km_kg, 7))
        calibrated_cats[c] = dict(
            base_l_per_km=base_rounded,
            load_l_per_km_kg=load_rounded,
        )
        category_diagnostics[c] = dict(
            uncalibrated_base_l_per_km=s["fuel_model"]["categories"][c]["base_l_per_km"],
            uncalibrated_load_l_per_km_kg=s["fuel_model"]["categories"][c]["load_l_per_km_kg"],
            calibrated_base_l_per_km=base_rounded,
            calibrated_load_l_per_km_kg=load_rounded,
            sample_count=len(xs),
            rmse_l_per_km=round(rmse_rate, 6),
            mae_l_per_km=round(mae_rate, 6),
            r2=round(r2, 4),
        )
        for d_m, m_kg, true_l in raw_pairs:
            pred_l = (d_m / 1000.0) * (base_rounded + load_rounded * m_kg)
            all_true_l.append(true_l)
            all_pred_l.append(pred_l)

    n_all = max(1, len(all_true_l))
    overall_rmse_l = math.sqrt(sum((t - p) ** 2 for t, p in zip(all_true_l, all_pred_l)) / n_all)
    overall_mae_l = sum(abs(t - p) for t, p in zip(all_true_l, all_pred_l)) / n_all
    mean_true = sum(all_true_l) / n_all if all_true_l else 1.0
    rel_bias_pct = ((sum(all_pred_l) - sum(all_true_l)) / max(1e-9, sum(all_true_l))) * 100.0

    idle_policy = s["fuel_model"]["idle_policy"]
    idle_rate = round(IDLE_FUEL_L_PER_S, 6) if idle_policy == "waiting_and_service" else 0.0

    validity_bounds = dict(
        mass_kg_min=0.0,
        mass_kg_max=max(1.0, round(cap_mass, 2)),
        speed_kmh_min=round(min(all_speeds) if all_speeds else 10.0, 2),
        speed_kmh_max=round(max(all_speeds) if all_speeds else 80.0, 2),
    )
    calibration_metadata = dict(
        simulator="longitudinal-tractive-v1",
        seed=int(seed),
        curb_mass_kg=float(curb_mass_kg),
        rmse_l=round(overall_rmse_l, 6),
        mae_l=round(overall_mae_l, 6),
        relative_bias_pct=round(rel_bias_pct, 4),
        validity_bounds=validity_bounds,
    )
    calibrated_model = dict(
        version="linear-load-v1",
        kind="load_dependent",
        calibration="microsim_calibrated",
        label="microsim-calibrated estimate",
        categories=calibrated_cats,
        idle_policy=idle_policy,
        idle_l_per_s=idle_rate,
        calibration_metadata=calibration_metadata,
    )
    return dict(
        calibrated_fuel_model=calibrated_model,
        category_diagnostics=category_diagnostics,
        residuals=dict(
            rmse_l=round(overall_rmse_l, 6),
            mae_l=round(overall_mae_l, 6),
            relative_bias_pct=round(rel_bias_pct, 4),
            mean_sample_fuel_l=round(mean_true, 6),
        ),
        validity_bounds=validity_bounds,
    )


def evaluate_microsim(
    scenario,
    solution,
    *,
    seed=42,
    congestion_factor=1.15,
    signal_delay_scale=1.0,
    curb_mass_kg=DEFAULT_CURB_MASS_KG,
):
    """Replay a validated feasible solution in the traffic microsimulator and benchmark fuel calibration."""
    if type(seed) is not int:
        raise InputError("seed must be an integer")
    if (
        not isinstance(congestion_factor, (int, float))
        or isinstance(congestion_factor, bool)
        or not math.isfinite(congestion_factor)
        or not (0.8 <= congestion_factor <= 2.5)
    ):
        raise InputError("congestion_factor must be a finite number in [0.8, 2.5]")
    if (
        not isinstance(signal_delay_scale, (int, float))
        or isinstance(signal_delay_scale, bool)
        or not math.isfinite(signal_delay_scale)
        or not (0.0 <= signal_delay_scale <= 3.0)
    ):
        raise InputError("signal_delay_scale must be a finite number in [0.0, 3.0]")

    s = validate_scenario(scenario)
    baseline = validate_solution(s, solution)
    if not baseline["feasible"]:
        raise InputError("microsimulation requires a deterministically feasible baseline solution")

    # Build both an uncalibrated scenario and a microsim-calibrated scenario to separate fuel metrics
    cal_bundle = calibrate_fuel_model(
        s,
        seed=seed,
        congestion_factor=congestion_factor,
        signal_delay_scale=signal_delay_scale,
        curb_mass_kg=curb_mass_kg,
    )

    s_uncal = copy.deepcopy(s)
    if s_uncal["fuel_model"]["calibration"] != "uncalibrated":
        # Restore standard uncalibrated coefficients for the uncalibrated comparison column
        from .drafts import DRAFT_PARAMS
        s_uncal["fuel_model"] = copy.deepcopy(DRAFT_PARAMS["fuel_model"])
    rep_uncal = validate_solution(validate_scenario(s_uncal), solution)

    s_cal = copy.deepcopy(s)
    s_cal["fuel_model"] = copy.deepcopy(cal_bundle["calibrated_fuel_model"])
    rep_cal = validate_solution(validate_scenario(s_cal), solution)

    stops = {stop["id"]: stop for stop in s["stops"]}
    legs = {(leg["source"], leg["target"]): leg for leg in s["legs"]}
    nominal_visits = {
        v["stop_id"]: v for r in baseline["vehicles"] for v in r["visits"]
    }

    rng = random.Random(seed + 1000)
    brk = s.get("driver_break")
    brk_enabled = bool(brk and brk.get("enabled"))

    total_nom_travel_s = 0.0
    total_sim_travel_s = 0.0
    total_signal_delay_s = 0.0
    total_sim_fuel_l = 0.0
    total_sim_co2_kg = 0.0

    stop_reports = []
    vehicle_reports = []
    on_time_stops = 0
    all_caps_ok = True

    for route in solution["routes"]:
        vid = route["vehicle_id"]
        stop_ids = route["stop_ids"]
        if not stop_ids:
            continue

        now = float(s["start_s"])
        mass = sum(float(stops[sid].get("delivery_mass_kg", 0.0)) for sid in stop_ids)
        work_since_break_s = 0.0
        explicit_pids = route.get("path_ids")
        previous = s["depot_id"]

        v_nom_travel_s = 0.0
        v_sim_travel_s = 0.0
        v_signal_s = 0.0
        v_sim_fuel_l = 0.0

        for leg_idx, target in enumerate(stop_ids + [s["depot_id"]]):
            leg = legs[previous, target]
            win_close = stops[target]["window_close_s"] if target != s["depot_id"] else None
            exp_pid = (
                explicit_pids[leg_idx]
                if explicit_pids is not None and leg_idx < len(explicit_pids)
                else None
            )
            _, leg_travel_s, segments, _ = select_leg_path(
                s, leg, mass, now, window_close_s=win_close, explicit_path_id=exp_pid
            )
            sim_leg = simulate_leg_microsim(
                leg,
                segments,
                leg_travel_s,
                mass,
                rng=rng,
                congestion_factor=congestion_factor,
                signal_delay_scale=signal_delay_scale,
                curb_mass_kg=curb_mass_kg,
            )
            v_nom_travel_s += sim_leg["nominal_travel_s"]
            v_sim_travel_s += sim_leg["microsim_travel_s"]
            v_signal_s += sim_leg["signal_delay_s"]
            v_sim_fuel_l += sim_leg["fuel_l"]

            now += sim_leg["microsim_travel_s"]
            work_since_break_s += sim_leg["microsim_travel_s"]

            if target != s["depot_id"]:
                stop = stops[target]
                arrival = now
                start = max(arrival, float(stop["window_open_s"]))
                wait_s = start - arrival
                late_s = max(0.0, start - float(stop["window_close_s"]))
                if late_s <= 1e-9:
                    on_time_stops += 1
                    late_s = 0.0

                svc = s["service"]
                service_s = float(
                    svc["constant_s"]
                    if svc["mode"] == "constant"
                    else svc["fixed_s"] + svc["per_package_s"] * stop["packages"]
                )
                if s["fuel_model"]["idle_policy"] == "waiting_and_service":
                    v_sim_fuel_l += (wait_s + service_s) * IDLE_FUEL_L_PER_S

                now = start + service_s
                work_since_break_s += service_s
                if brk_enabled and work_since_break_s >= float(brk["threshold_s"]):
                    now += float(brk["duration_s"])
                    work_since_break_s = 0.0

                mass = mass - float(stop.get("delivery_mass_kg", 0.0)) + float(stop["mass_kg"])
                nom_v = nominal_visits.get(target, {})
                stop_reports.append(
                    dict(
                        stop_id=target,
                        vehicle_id=vid,
                        window_open_s=stop["window_open_s"],
                        window_close_s=stop["window_close_s"],
                        nominal_arrival_s=nom_v.get("arrival_s", 0),
                        microsim_arrival_s=round(arrival, 1),
                        arrival_drift_s=round(arrival - float(nom_v.get("arrival_s", 0)), 1),
                        lateness_s=round(late_s, 1),
                        on_time=late_s == 0.0,
                    )
                )
            previous = target

        v_duration_s = now - float(s["start_s"])
        cap_ok = s["duration_cap_s"] is None or v_duration_s <= float(s["duration_cap_s"]) + 1e-9
        if not cap_ok:
            all_caps_ok = False

        uncal_v = next(v for v in rep_uncal["vehicles"] if v["vehicle_id"] == vid)
        cal_v = next(v for v in rep_cal["vehicles"] if v["vehicle_id"] == vid)
        vehicle_reports.append(
            dict(
                vehicle_id=vid,
                stops_count=len(stop_ids),
                nominal_travel_s=round(v_nom_travel_s, 1),
                microsim_travel_s=round(v_sim_travel_s, 1),
                signal_delay_s=round(v_signal_s, 1),
                microsim_duration_s=round(v_duration_s, 1),
                duration_cap_ok=cap_ok,
                uncalibrated_fuel_l=round(uncal_v["metrics"]["estimated_fuel_l"], 4),
                microsim_fuel_l=round(v_sim_fuel_l, 4),
                calibrated_fuel_l=round(cal_v["metrics"]["estimated_fuel_l"], 4),
                co2_kg=round(v_sim_fuel_l * CO2_KG_PER_L_DIESEL, 3),
            )
        )

        total_nom_travel_s += v_nom_travel_s
        total_sim_travel_s += v_sim_travel_s
        total_signal_delay_s += v_signal_s
        total_sim_fuel_l += v_sim_fuel_l
        total_sim_co2_kg += v_sim_fuel_l * CO2_KG_PER_L_DIESEL

    uncal_fuel_l = float(rep_uncal["metrics"]["estimated_fuel_l"])
    cal_fuel_l = float(rep_cal["metrics"]["estimated_fuel_l"])
    uncal_err_l = uncal_fuel_l - total_sim_fuel_l
    cal_err_l = cal_fuel_l - total_sim_fuel_l
    uncal_err_pct = (abs(uncal_err_l) / max(1e-9, total_sim_fuel_l)) * 100.0
    cal_err_pct = (abs(cal_err_l) / max(1e-9, total_sim_fuel_l)) * 100.0
    drift_s = total_sim_travel_s - total_nom_travel_s
    drift_pct = (drift_s / max(1.0, total_nom_travel_s)) * 100.0

    total_stops = max(1, len(s["stops"]))
    return dict(
        schema_version=1,
        scenario_id=s["id"],
        simulator=dict(
            name="longitudinal-tractive-v1",
            seed=seed,
            congestion_factor=congestion_factor,
            signal_delay_scale=signal_delay_scale,
            curb_mass_kg=curb_mass_kg,
            telemetry_disclaimer="Physical tractive-power microsimulation estimate; not physical vehicle telemetry.",
        ),
        timing_drift=dict(
            nominal_travel_s=round(total_nom_travel_s, 1),
            microsim_travel_s=round(total_sim_travel_s, 1),
            signal_delay_s=round(total_signal_delay_s, 1),
            travel_drift_s=round(drift_s, 1),
            travel_drift_pct=round(drift_pct, 2),
            window_compliance_rate=round(on_time_stops / total_stops, 4),
            duration_cap_ok=all_caps_ok,
        ),
        fuel_comparison=dict(
            uncalibrated_parametric=dict(
                fuel_l=round(uncal_fuel_l, 4),
                label=rep_uncal["fuel_label"],
                signed_error_l=round(uncal_err_l, 4),
                abs_error_pct=round(uncal_err_pct, 2),
            ),
            microsim_reference=dict(
                fuel_l=round(total_sim_fuel_l, 4),
                co2_kg=round(total_sim_co2_kg, 3),
                label="longitudinal-tractive-v1 physical microsim reference",
            ),
            calibrated_parametric=dict(
                fuel_l=round(cal_fuel_l, 4),
                label=rep_cal["fuel_label"],
                signed_error_l=round(cal_err_l, 4),
                abs_error_pct=round(cal_err_pct, 2),
            ),
        ),
        calibration=cal_bundle,
        vehicle_reports=vehicle_reports,
        stop_reports=stop_reports,
    )


def parse_sumo_emission_xml(xml_text):
    """Parse an Eclipse SUMO `<emission-export>` XML document into per-vehicle fuel & travel summaries.

    In SUMO emission output, `fuel` is reported in mg/s (or ml/s depending on emissionClass);
    standard HBEFA3/4 SUMO diesel output reports `fuel` in ml/s (or mg/s when > 10). We normalize
    automatically and record the parsed totals.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise InputError(f"invalid SUMO emission XML: {exc}") from exc
    if root.tag != "emission-export":
        raise InputError("root XML element must be <emission-export>")

    vehicles = {}
    timesteps = 0
    for ts in root.findall("timestep"):
        timesteps += 1
        for veh in ts.findall("vehicle"):
            vid = veh.attrib.get("id", "unknown")
            speed = float(veh.attrib.get("speed", "0"))
            waiting = float(veh.attrib.get("waiting", "0"))
            raw_fuel = float(veh.attrib.get("fuel", "0"))
            # If raw_fuel > 50, SUMO HBEFA reported mg/s (~832 mg/ml diesel); otherwise ml/s
            fuel_l_step = (raw_fuel / 832_000.0) if raw_fuel > 50.0 else (raw_fuel / 1000.0)
            entry = vehicles.setdefault(
                vid,
                dict(vehicle_id=vid, travel_s=0, waiting_s=0.0, distance_m=0.0, fuel_l=0.0),
            )
            entry["travel_s"] += 1
            if speed < 0.1 or waiting > 0:
                entry["waiting_s"] += 1.0
            entry["distance_m"] += speed
            entry["fuel_l"] += fuel_l_step

    vehicle_list = [
        dict(
            vehicle_id=v["vehicle_id"],
            travel_s=v["travel_s"],
            waiting_s=round(v["waiting_s"], 1),
            distance_m=round(v["distance_m"], 2),
            fuel_l=round(v["fuel_l"], 5),
            co2_kg=round(v["fuel_l"] * CO2_KG_PER_L_DIESEL, 4),
        )
        for v in sorted(vehicles.values(), key=lambda item: item["vehicle_id"])
    ]
    return dict(
        simulator="sumo-emission-xml-v1",
        timesteps=timesteps,
        vehicles=vehicle_list,
        totals=dict(
            travel_s=sum(v["travel_s"] for v in vehicle_list),
            waiting_s=round(sum(v["waiting_s"] for v in vehicle_list), 1),
            distance_m=round(sum(v["distance_m"] for v in vehicle_list), 2),
            fuel_l=round(sum(v["fuel_l"] for v in vehicle_list), 5),
            co2_kg=round(sum(v["co2_kg"] for v in vehicle_list), 4),
        ),
    )
