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


class Features:
    def __init__(self, scenario, routes, validation):
        if not validation['feasible']:
            raise InputError('features require a validated current solution')
        self.s, self.routes, self.report = scenario, routes, validation
        self.stops = {s['id']: s for s in scenario['stops']}
        self.visits = {v['stop_id']: v for r in validation['vehicles'] for v in r['visits']}
        self.legs = {}
        for leg in scenario['legs']:
            if leg['reachable']:
                fuel = sum(seg['distance_m'] / 1000 * scenario['fuel_model']['categories'][seg['driving_category']]['base_l_per_km'] for seg in leg['segments'])
                self.legs[leg['source'], leg['target']] = (leg['travel_s'], fuel)
        self.current_arcs = self.arc_cost(routes)

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
                  sum(self.stops[v]['mass_kg'] for v in routes[a])/capacity['mass_kg'],
                  sum(self.stops[v]['volume_m3'] for v in routes[a])/capacity['volume_m3']]
        second = move.j - 1 if move.operator == 'reverse' else move.j
        for a, i in ((move.a, move.i), (move.b, second)):
            if i >= len(routes[a]):
                x += [0.] * 6
            else:
                stop = self.stops[routes[a][i]]; visit = self.visits[stop['id']]
                x += [stop['mass_kg']/capacity['mass_kg'], stop['volume_m3']/capacity['volume_m3'],
                      (stop['window_open_s']-s['start_s'])/ts, (stop['window_close_s']-s['start_s'])/ts,
                      (stop['window_close_s']-visit['service_start_s'])/ts, 1.]
        after = self.arc_cost(move.apply(routes))
        x += [(after[0]-self.current_arcs[0])/ts, (after[1]-self.current_arcs[1])/fs]
        if len(x) != 30 or not all(math.isfinite(v) for v in x):
            raise InputError('nonfinite or incompatible feature vector')
        return x


def signed(value, key):
    return dict(value, **{key: scenario_hash(value)})


def verify_hash(value, key):
    if value.get(key) != scenario_hash({k:v for k,v in value.items() if k != key}):
        raise InputError(f'{key} mismatch')


class Policy:
    def __init__(self, model):
        import numpy as np
        verify_hash(model, 'model_hash')
        if model.get('schema_version') != 1 or model.get('kind') != 'learned_policy' or model.get('feature_schema') != FEATURE_SCHEMA or model.get('architecture') != [30,32,16,2]:
            raise InputError('incompatible learned checkpoint')
        self.model = model
        self.layers = []
        if len(model['layers']) != 3: raise InputError('expected three model layers')
        for layer, ni, no in zip(model['layers'], (30,32,16), (32,16,2)):
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
        if x.ndim != 2 or x.shape[1] != 30 or not np.isfinite(x).all(): raise InputError('invalid model input')
        for index, (w,b) in enumerate(self.layers):
            x = x @ w.T + b
            if index < 2: x = np.maximum(x, np.float32(0))
        # Sigmoid and softplus match training's probability and positive gain heads.
        probability = 1 / (1 + np.exp(-np.clip(x[:,0], -80, 80)))
        gain = np.logaddexp(np.float32(0), x[:,1])
        return probability * gain

    def order(self, scenario, routes, validation, moves):
        started = time.monotonic()
        extractor = Features(scenario, routes, validation)
        scores = self.predict([extractor.row(m) for m in moves]) if moves else []
        ordered = sorted(range(len(moves)), key=lambda i: -float(scores[i]))
        self.inference_s += time.monotonic()-started
        return [moves[i] for i in ordered]
