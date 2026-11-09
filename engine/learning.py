"""M4 feature schema and portable FP32 policy. No candidate labels enter inference."""
import math
import time
from pathlib import Path

from .contracts import InputError, load_json
from .solver import scenario_hash
from .validation import objective_score

FEATURES = ['operator_relocate', 'operator_swap', 'operator_reverse', 'time_weight', 'fuel_weight', 'current_score']
for side in ('source', 'destination'):
    FEATURES += [f'{side}_{key}' for key in ('route_length', 'position', 'duration', 'mass', 'volume')]
for side in ('first', 'second'):
    FEATURES += [f'{side}_{key}' for key in ('mass', 'volume', 'window_open', 'window_close', 'slack', 'present')]
FEATURES += ['delta_driving_time', 'delta_zero_load_fuel']
FEATURE_SCHEMA = dict(version=1, names=FEATURES, normalization='stop-count-capacity-scenario-scales-v1')

FEATURES_V2 = FEATURES + [
    'stop_count_norm',
    'fleet_size_norm',
    'service_mode_packages',
    'has_duration_cap',
    'source_cap_utilization',
    'destination_cap_utilization',
    'delta_loaded_fuel',
    'window_urgency_delta',
]
FEATURE_SCHEMA_V2 = dict(version=2, names=FEATURES_V2, normalization='stop-count-capacity-scenario-scales-v2')

FEATURES_V3 = FEATURES_V2 + [
    'source_delivery_mass_ratio',
    'dest_delivery_mass_ratio',
    'has_driver_break',
    'source_break_urgency',
    'dest_break_urgency',
    'has_path_alternatives',
    'is_fuel_calibrated',
    'traffic_slow_urban_ratio',
]
FEATURE_SCHEMA_V3 = dict(version=3, names=FEATURES_V3, normalization='stop-count-capacity-scenario-scales-v3')

FEATURES_V4 = FEATURES_V3 + [
    'routes_centroid_dist_norm',
    'routes_bbox_overlap_ratio',
]
FEATURE_SCHEMA_V4 = dict(version=4, names=FEATURES_V4, normalization='scale-invariant-bologna-v4')

# version -> (feature schema, MLP architecture); the single source for training, export and inference.
POLICY_SPECS = {1: (FEATURE_SCHEMA, [30,32,16,2]), 2: (FEATURE_SCHEMA_V2, [38,64,32,2]),
                3: (FEATURE_SCHEMA_V3, [46,64,32,2]), 4: (FEATURE_SCHEMA_V4, [48,64,32,2])}


class Features:
    def __init__(self, scenario, routes, validation, version=1):
        if not validation['feasible']:
            raise InputError('features require a validated current solution')
        if version not in (1, 2, 3, 4):
            raise InputError('unsupported feature schema version')
        self.version = version
        self.s, self.routes, self.report = scenario, routes, validation
        self.stops = {s['id']: s for s in scenario['stops']}
        self.visits = {v['stop_id']: v for r in validation['vehicles'] for v in r['visits']}
        self.legs = {}
        self.leg_load_coef = {}
        for leg in scenario['legs']:
            if leg['reachable']:
                fuel = sum(seg['distance_m'] / 1000 * scenario['fuel_model']['categories'][seg['driving_category']]['base_l_per_km'] for seg in leg['segments'])
                load_coef = sum(seg['distance_m'] / 1000 * scenario['fuel_model']['categories'][seg['driving_category']]['load_l_per_km_kg'] for seg in leg['segments'])
                self.legs[leg['source'], leg['target']] = (leg['travel_s'], fuel)
                self.leg_load_coef[leg['source'], leg['target']] = load_coef
        self.route_mass = [sum(self.stops[v]['mass_kg'] for v in route) for route in routes]
        self.route_volume = [sum(self.stops[v]['volume_m3'] for v in route) for route in routes]
        self.route_arcs = [self._single_route_arc_cost(route) for route in routes]
        self.current_arcs = self.arc_cost(routes)
        if version in (3, 4):
            veh_caps = scenario.get('vehicle_capacities') or {}
            self.capacities = [veh_caps.get(vid, scenario['capacity']) for vid in scenario['vehicle_ids']]
            self.route_delivery_mass = [sum(self.stops[v].get('delivery_mass_kg', 0) for v in route) for route in routes]
            self.leg_has_alts = {(leg['source'], leg['target']): bool(leg.get('alternatives')) for leg in scenario['legs']}
            self.leg_dist = {}  # (source, target) -> (slow/urban distance_m, total distance_m)
            for leg in scenario['legs']:
                segs = leg.get('segments', [])
                total = sum(float(seg.get('distance_m', 0)) for seg in segs)
                slow = sum(float(seg.get('distance_m', 0)) for seg in segs
                           if 'slow' in seg.get('driving_category', '') or 'urban' in seg.get('driving_category', ''))
                self.leg_dist[leg['source'], leg['target']] = (slow, total)
            brk = scenario.get('driver_break')
            self.has_driver_break = 1.0 if (brk and brk.get('enabled')) else 0.0
            if self.has_driver_break:
                thresh = max(1.0, float(brk.get('threshold_s', 14400)))
                urgencies = []
                for v_rep in self.report['vehicles']:
                    work = 0.0
                    v_visits = {v['stop_id']: v for v in v_rep.get('visits', [])}
                    for leg in v_rep.get('legs', []):
                        work += float(leg.get('travel_s', 0))
                        target = leg.get('target')
                        if target != self.s['depot_id']:
                            vis = v_visits.get(target)
                            if vis:
                                dur = float(vis.get('departure_s', 0)) - float(vis.get('service_start_s', 0)) - float(vis.get('break_s', 0))
                                work += max(0.0, dur)
                                if vis.get('break_s', 0) > 0:
                                    work = 0.0
                    urgencies.append(min(1.0, max(0.0, work / thresh)))
                self.route_break_urgency = urgencies
            else:
                self.route_break_urgency = [0.0] * len(routes)
        if version == 4:
            from .moves import compute_route_geometry
            locs = {loc['id']: loc for loc in scenario.get('locations', [])}
            depot_loc = locs.get(scenario['depot_id'], {}).get('snapped', [11.3426, 44.4949])
            self.route_centroids, self.route_bboxes = compute_route_geometry(routes, locs, depot_loc)

    def _single_route_arc_cost(self, route):
        if not route:
            return (0., 0.)
        t_sum, f_sum = 0., 0.
        depot = self.s['depot_id']
        sentinel = (self.s['objective']['time_scale_s'], self.s['objective']['fuel_scale_l'])
        prev = depot
        for node in route:
            cost = self.legs.get((prev, node), sentinel)
            t_sum += cost[0]; f_sum += cost[1]
            prev = node
        cost = self.legs.get((prev, depot), sentinel)
        return (t_sum + cost[0], f_sum + cost[1])

    def arc_cost(self, routes):
        result = [0., 0.]
        for route in routes:
            if not route: continue
            nodes = [self.s['depot_id'], *route, self.s['depot_id']]
            for a, b in zip(nodes, nodes[1:]):
                # Unreachable arcs have explicit finite sentinel costs; no substitute road.
                cost = self.legs.get((a, b), (self.s['objective']['time_scale_s'], self.s['objective']['fuel_scale_l']))
                for k in range(2): result[k] += cost[k]
        return result

    def row(self, move):
        s, routes = self.s, self.routes
        n = max(1, len(s['stops'])); ts = s['objective']['time_scale_s']; fs = s['objective']['fuel_scale_l']
        capacity = s['capacity']
        x = [float(move.operator == op) for op in ('relocate', 'swap', 'reverse')]
        x += [s['objective']['time_weight'], s['objective']['fuel_weight'], objective_score(s, self.report)]
        for a, i in ((move.a, move.i), (move.b, move.j)):
            r = self.report['vehicles'][a]
            x += [len(routes[a])/n, i/n, r['metrics']['fleet_elapsed_s']/ts,
                  self.route_mass[a]/capacity['mass_kg'],
                  self.route_volume[a]/capacity['volume_m3']]
        second = move.j - 1 if move.operator == 'reverse' else move.j
        first_stop = self.stops[routes[move.a][move.i]] if move.i < len(routes[move.a]) else None
        second_stop = self.stops[routes[move.b][second]] if second < len(routes[move.b]) else None
        for stop in (first_stop, second_stop):
            if stop is None:
                x += [0.] * 6
            else:
                visit = self.visits[stop['id']]
                x += [stop['mass_kg']/capacity['mass_kg'], stop['volume_m3']/capacity['volume_m3'],
                      (stop['window_open_s']-s['start_s'])/ts, (stop['window_close_s']-s['start_s'])/ts,
                      (stop['window_close_s']-visit['service_start_s'])/ts, 1.]
        if self.version == 1 and len(s['stops']) <= 20:
            after = self.arc_cost(move.apply(routes))
            dt = (after[0]-self.current_arcs[0])/ts
            df = (after[1]-self.current_arcs[1])/fs
        else:
            applied = move.apply(routes)
            if move.a == move.b:
                old_t, old_f = self.route_arcs[move.a]
                new_t, new_f = self._single_route_arc_cost(applied[move.a])
            else:
                old_t = self.route_arcs[move.a][0] + self.route_arcs[move.b][0]
                old_f = self.route_arcs[move.a][1] + self.route_arcs[move.b][1]
                ca = self._single_route_arc_cost(applied[move.a])
                cb = self._single_route_arc_cost(applied[move.b])
                new_t, new_f = ca[0] + cb[0], ca[1] + cb[1]
            dt = (new_t - old_t) / ts
            df = (new_f - old_f) / fs
        x += [dt, df]
        if self.version in (2, 3, 4):
            cap_s = s['duration_cap_s']
            src_dur = self.report['vehicles'][move.a]['metrics']['fleet_elapsed_s']
            dst_dur = self.report['vehicles'][move.b]['metrics']['fleet_elapsed_s']
            src_cap_util = (src_dur / max(1., float(cap_s))) if cap_s is not None else 0.
            dst_cap_util = (dst_dur / max(1., float(cap_s))) if cap_s is not None else 0.
            moved_mass = first_stop['mass_kg'] if first_stop is not None else 0.
            loaded_fuel_delta = df * (1. + moved_mass / max(1., float(capacity['mass_kg'])))
            urgency_a = (first_stop['window_close_s'] - s['start_s']) / ts if first_stop is not None else 0.
            urgency_b = (second_stop['window_close_s'] - s['start_s']) / ts if second_stop is not None else urgency_a
            stop_count_norm = (math.log1p(len(s['stops'])) / math.log1p(2000.)) if self.version == 4 else (len(s['stops']) / 100.)
            fleet_size_norm = (math.log1p(len(s['vehicle_ids'])) / math.log1p(100.)) if self.version == 4 else (len(s['vehicle_ids']) / 5.)
            x += [
                stop_count_norm,
                fleet_size_norm,
                1. if s['service']['mode'] == 'packages' else 0.,
                1. if cap_s is not None else 0.,
                src_cap_util,
                dst_cap_util,
                loaded_fuel_delta,
                urgency_b - urgency_a,
            ]
        if self.version in (3, 4):
            cap_a = self.capacities[move.a] if move.a < len(self.capacities) else s['capacity']
            cap_b = self.capacities[move.b] if move.b < len(self.capacities) else s['capacity']
            src_deliv_ratio = self.route_delivery_mass[move.a] / max(1.0, float(cap_a.get('mass_kg', 1.0)))
            dst_deliv_ratio = self.route_delivery_mass[move.b] / max(1.0, float(cap_b.get('mass_kg', 1.0)))
            src_brk_urg = self.route_break_urgency[move.a] if move.a < len(self.route_break_urgency) else 0.0
            dst_brk_urg = self.route_break_urgency[move.b] if move.b < len(self.route_break_urgency) else 0.0

            affected_legs = []
            for veh_idx, pos in ((move.a, move.i), (move.b, second)):
                if veh_idx < len(routes) and pos < len(routes[veh_idx]):
                    r = routes[veh_idx]
                    prev_node = r[pos - 1] if pos > 0 else s['depot_id']
                    curr_node = r[pos]
                    next_node = r[pos + 1] if pos + 1 < len(r) else s['depot_id']
                    affected_legs.append((prev_node, curr_node))
                    affected_legs.append((curr_node, next_node))

            if affected_legs:
                has_alts_ratio = sum(1.0 for leg in affected_legs if self.leg_has_alts.get(leg, False)) / len(affected_legs)
                slow_dist = sum(self.leg_dist.get(leg, (0.0, 0.0))[0] for leg in affected_legs)
                tot_dist = sum(self.leg_dist.get(leg, (0.0, 0.0))[1] for leg in affected_legs)
                traffic_ratio = (slow_dist / tot_dist) if tot_dist > 0.0 else 0.0
            else:
                has_alts_ratio = 0.0
                traffic_ratio = 0.0

            is_calibrated = 1.0 if s.get('fuel_model', {}).get('calibration') == 'microsim_calibrated' else 0.0

            x += [
                src_deliv_ratio,
                dst_deliv_ratio,
                self.has_driver_break,
                src_brk_urg,
                dst_brk_urg,
                has_alts_ratio,
                is_calibrated,
                traffic_ratio,
            ]
        if self.version == 4:
            ca = self.route_centroids[move.a]
            cb = self.route_centroids[move.b]
            deg_dist = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
            centroid_dist_norm = min(1.0, deg_dist / 0.25)

            ba = self.route_bboxes[move.a]
            bb = self.route_bboxes[move.b]
            inter_w = max(0.0, min(ba[2], bb[2]) - max(ba[0], bb[0]))
            inter_h = max(0.0, min(ba[3], bb[3]) - max(ba[1], bb[1]))
            inter_area = inter_w * inter_h
            area_a = max(1e-9, (ba[2] - ba[0]) * (ba[3] - ba[1]))
            area_b = max(1e-9, (bb[2] - bb[0]) * (bb[3] - bb[1]))
            union_area = area_a + area_b - inter_area
            bbox_overlap_ratio = min(1.0, max(0.0, inter_area / union_area)) if union_area > 0 else 0.0

            x += [centroid_dist_norm, bbox_overlap_ratio]
        expected_len = POLICY_SPECS[self.version][1][0]
        if len(x) != expected_len or not all(math.isfinite(v) for v in x):
            raise InputError('nonfinite or incompatible feature vector')
        return x


def signed(value, key):
    raw = {k: v for k, v in value.items() if k != key}
    return dict(raw, **{key: scenario_hash(raw)})


def verify_hash(value, key):
    if value.get(key) != scenario_hash({k:v for k,v in value.items() if k != key}):
        raise InputError(f'{key} mismatch')


class Policy:
    def __init__(self, model):
        import numpy as np
        verify_hash(model, 'model_hash')
        schema_ver = model.get('schema_version')
        if schema_ver not in POLICY_SPECS:
            raise InputError('incompatible learned checkpoint')
        schema, arch = POLICY_SPECS[schema_ver]
        if model.get('kind') != 'learned_policy' or model.get('feature_schema') != schema or model.get('architecture') != arch:
            raise InputError(f'incompatible learned v{schema_ver} checkpoint')
        expected_shapes = tuple(zip(arch, arch[1:]))
        self.feature_version = schema_ver
        self.input_dim = arch[0]
        self.model = model
        self.layers = []
        if len(model['layers']) != 3: raise InputError('expected three model layers')
        for layer, (ni, no) in zip(model['layers'], expected_shapes):
            w = np.asarray(layer['weight'], dtype=np.float32); b = np.asarray(layer['bias'], dtype=np.float32)
            if w.shape != (no,ni) or b.shape != (no,) or not np.isfinite(w).all() or not np.isfinite(b).all():
                raise InputError('invalid model layer')
            self.layers.append((w,b))
        self.inference_s = 0.

    @classmethod
    def load(cls, path):
        if not path or not Path(path).is_file(): raise InputError('learned policy requires an existing checkpoint')
        return cls(load_json(path))

    def predict(self, features):
        import numpy as np
        x = np.asarray(features, dtype=np.float32)
        if x.ndim != 2 or x.shape[1] != self.input_dim or not np.isfinite(x).all(): raise InputError('invalid model input')
        for index, (w,b) in enumerate(self.layers):
            x = x @ w.T + b
            if index < 2: x = np.maximum(x, np.float32(0))
        # Sigmoid and softplus match training's probability and positive gain heads.
        probability = 1 / (1 + np.exp(-np.clip(x[:,0], -80, 80)))
        gain = np.logaddexp(np.float32(0), x[:,1])
        return probability * gain

    def order(self, scenario, routes, validation, moves):
        started = time.monotonic()
        extractor = Features(scenario, routes, validation, version=self.feature_version)
        scores = self.predict([extractor.row(m) for m in moves]) if moves else []
        ordered = sorted(range(len(moves)), key=lambda i: -float(scores[i]))
        self.inference_s += time.monotonic()-started
        return [moves[i] for i in ordered]
