"""Tests for large fleet (up to 50 vehicles) scaling on single city (Bologna)."""
import math
import unittest

from engine.contracts import load_json
from engine.moves import (
    Move,
    compute_route_geometry,
    get_spatial_neighbor_routes,
    enumerate_moves,
)


class SpatialPruningTests(unittest.TestCase):
    def setUp(self):
        # 4 routes distributed across Bologna:
        # Route 0 (West / Borgo Panigale): lon ~ 11.28
        # Route 1 (Center / Navile): lon ~ 11.34
        # Route 2 (East / San Donato): lon ~ 11.37
        # Route 3 (Far East / San Lazzaro): lon ~ 11.45
        self.depot_coord = [11.3426, 44.4949]  # Bologna center
        self.locations = {
            "depot": {"snapped": [11.3426, 44.4949]},
            "w1": {"snapped": [11.2800, 44.5100]},
            "w2": {"snapped": [11.2850, 44.5150]},
            "c1": {"snapped": [11.3400, 44.5000]},
            "c2": {"snapped": [11.3450, 44.4980]},
            "e1": {"snapped": [11.3700, 44.5020]},
            "e2": {"snapped": [11.3750, 44.5050]},
            "fe1": {"snapped": [11.4500, 44.4700]},
            "fe2": {"snapped": [11.4550, 44.4720]},
        }
        self.routes = [
            ["w1", "w2"],
            ["c1", "c2"],
            ["e1", "e2"],
            ["fe1", "fe2"],
        ]

    def test_compute_route_geometry(self):
        centroids, bboxes = compute_route_geometry(self.routes, self.locations, self.depot_coord)
        self.assertEqual(len(centroids), 4)
        self.assertEqual(len(bboxes), 4)
        # Route 0 centroid lon around 11.2825
        self.assertAlmostEqual(centroids[0][0], 11.2825, places=3)
        # Route 3 centroid lon around 11.4525
        self.assertAlmostEqual(centroids[3][0], 11.4525, places=3)

    def test_compute_route_geometry_empty_route_falls_back_to_depot(self):
        routes = [["w1"], []]
        centroids, bboxes = compute_route_geometry(routes, self.locations, self.depot_coord)
        self.assertEqual(centroids[1], tuple(self.depot_coord))
        self.assertEqual(bboxes[1], (self.depot_coord[0], self.depot_coord[1], self.depot_coord[0], self.depot_coord[1]))

    def test_get_spatial_neighbor_routes(self):
        # With max_neighbors=1, route 0 should neighbor route 1 (closest), but not route 3
        neighbors = get_spatial_neighbor_routes(self.routes, self.locations, self.depot_coord, max_neighbors=1)
        self.assertIn(1, neighbors[0])
        self.assertNotIn(3, neighbors[0])

    def test_enumerate_moves_with_spatial_neighbors(self):
        all_moves = list(enumerate_moves(self.routes))
        # Now prune so route 0 only neighbors route 1, route 3 only neighbors route 2
        neighbors = {0: {1}, 1: {0, 2}, 2: {1, 3}, 3: {2}}
        pruned_moves = list(enumerate_moves(self.routes, spatial_neighbors=neighbors))

        self.assertLess(len(pruned_moves), len(all_moves))
        # Ensure intra-route reverses are identical
        rev_all = [m for m in all_moves if m.operator == 'reverse']
        rev_pruned = [m for m in pruned_moves if m.operator == 'reverse']
        self.assertEqual(len(rev_all), len(rev_pruned))

        # Ensure no cross-moves between route 0 and route 3
        cross_0_3 = [m for m in pruned_moves if (m.a == 0 and m.b == 3) or (m.a == 3 and m.b == 0)]
        self.assertEqual(len(cross_0_3), 0)


class SchemaV4Tests(unittest.TestCase):
    def test_features_v4_definition_and_extraction(self):
        from engine.learning import FEATURES_V4, FEATURE_SCHEMA_V4, Features, Policy, signed
        from engine.solver import proposal
        from engine.validation import validate_solution

        self.assertEqual(len(FEATURES_V4), 48)
        self.assertEqual(FEATURE_SCHEMA_V4["version"], 4)

        s = load_json("tests/fixtures/two-stops.json")
        sol = proposal(s, [["a"], ["b"]])
        val = validate_solution(s, sol)
        ext_v4 = Features(s, [["a"], ["b"]], val, version=4)
        moves = list(enumerate_moves([["a"], ["b"]]))
        row = ext_v4.row(moves[0])

        self.assertEqual(len(row), 48)
        self.assertTrue(all(math.isfinite(v) for v in row))

        # Check log-scale normalization for stop count (index 30) and fleet size (index 31)
        # 2 stops: log1p(2) / log1p(2000)
        expected_stops_norm = math.log1p(2) / math.log1p(2000)
        self.assertAlmostEqual(row[30], expected_stops_norm, places=4)
        # 2 vehicles: log1p(2) / log1p(100)
        expected_fleet_norm = math.log1p(2) / math.log1p(100)
        self.assertAlmostEqual(row[31], expected_fleet_norm, places=4)

        # Check spatial route features (index 46 and 47)
        # 46: routes_centroid_dist_norm in [0, 1]
        self.assertTrue(0.0 <= row[46] <= 1.0)
        # 47: routes_bbox_overlap_ratio in [0, 1]
        self.assertTrue(0.0 <= row[47] <= 1.0)

    def test_policy_v4_loading_and_prediction(self):
        from engine.learning import FEATURE_SCHEMA_V4, Policy, signed

        layers = [
            dict(weight=[[0.01] * 48 for _ in range(64)], bias=[0.0] * 64),
            dict(weight=[[0.01] * 64 for _ in range(32)], bias=[0.0] * 32),
            dict(weight=[[0.01] * 32 for _ in range(2)], bias=[0.0] * 2),
        ]
        model = signed(
            dict(
                schema_version=4,
                kind="learned_policy",
                architecture=[48, 64, 32, 2],
                feature_schema=FEATURE_SCHEMA_V4,
                metadata=dict(test=True),
                layers=layers,
            ),
            "model_hash",
        )
        policy = Policy(model)
        self.assertEqual(policy.feature_version, 4)
        self.assertEqual(policy.input_dim, 48)

        preds = policy.predict([[0.1] * 48, [0.5] * 48])
        self.assertEqual(len(preds), 2)
        self.assertTrue(all(p > 0 for p in preds))


class DraftLargeFleetTests(unittest.TestCase):
    def test_make_draft_large_fleet_bologna(self):
        from engine.city import CityRegistry
        from engine.drafts import make_draft, validate_draft

        registry = CityRegistry("data")
        manifest = registry.get("bologna")

        # Draft with 50 vehicles and 300 stops
        draft = make_draft(manifest, seed=42, count=300, vehicles=50)
        self.assertEqual(len(draft["stops"]), 300)
        self.assertEqual(len(draft["parameters"]["vehicle_ids"]), 50)
        # Validate draft structure against schema
        validated = validate_draft(draft, manifest)
        self.assertEqual(len(validated["stops"]), 300)


class LargeFleetBenchmarkTests(unittest.TestCase):
    def test_spatial_pruning_dramatic_move_reduction_on_large_fleet(self):
        # 20 vehicles, each with 5 stops (100 stops total)
        routes = []
        locs = {}
        for v in range(20):
            r = []
            for s in range(5):
                sid = f"stop_{v}_{s}"
                r.append(sid)
                # Spread vehicles across lon 11.20 to 11.45 (Bologna west-to-east)
                locs[sid] = {"snapped": [11.20 + (v / 20.0) * 0.25, 44.45 + (s / 5.0) * 0.05]}
            routes.append(r)

        depot = [11.3426, 44.4949]
        neighbors = get_spatial_neighbor_routes(routes, locs, depot, max_neighbors=4)

        all_moves = list(enumerate_moves(routes))
        pruned_moves = list(enumerate_moves(routes, spatial_neighbors=neighbors))

        # Check move reduction
        self.assertLess(len(pruned_moves), len(all_moves))
        reduction_pct = 100.0 * (len(all_moves) - len(pruned_moves)) / len(all_moves)
        # Should prune at least 65% of cross-route combinations
        self.assertGreater(reduction_pct, 65.0)

    def test_policy_v4_order_latency_under_100ms(self):
        import time
        from engine.learning import FEATURE_SCHEMA_V4, Policy, signed
        from engine.solver import proposal
        from engine.validation import validate_solution

        s = load_json("tests/fixtures/two-stops.json")
        sol = proposal(s, [["a"], ["b"]])
        val = validate_solution(s, sol)

        layers = [
            dict(weight=[[0.01] * 48 for _ in range(64)], bias=[0.0] * 64),
            dict(weight=[[0.01] * 64 for _ in range(32)], bias=[0.0] * 32),
            dict(weight=[[0.01] * 32 for _ in range(2)], bias=[0.0] * 2),
        ]
        model = signed(
            dict(
                schema_version=4,
                kind="learned_policy",
                architecture=[48, 64, 32, 2],
                feature_schema=FEATURE_SCHEMA_V4,
                metadata=dict(test=True),
                layers=layers,
            ),
            "model_hash",
        )
        policy = Policy(model)

        # Benchmark ordering 200 moves
        base_moves = list(enumerate_moves([["a"], ["b"]]))
        test_moves = (base_moves * 50)[:200]  # 200 moves

        start = time.perf_counter()
        ordered = policy.order(s, [["a"], ["b"]], val, test_moves)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        self.assertEqual(len(ordered), len(test_moves))
        self.assertLess(elapsed_ms, 100.0)  # sub-100ms latency


class TrainV4Tests(unittest.TestCase):
    def test_export_model_and_make_network_v4(self):
        try:
            import torch
            from engine.learn_train import export_model, make_network
            from engine.learning import FEATURE_SCHEMA_V4

            net = make_network(version=4)
            self.assertEqual(net[0].in_features, 48)
            self.assertEqual(net[0].out_features, 64)
            self.assertEqual(net[2].in_features, 64)
            self.assertEqual(net[2].out_features, 32)
            self.assertEqual(net[4].in_features, 32)
            self.assertEqual(net[4].out_features, 2)

            exported = export_model(net, dict(test="v4"), version=4)
            self.assertEqual(exported["schema_version"], 4)
            self.assertEqual(exported["architecture"], [48, 64, 32, 2])
            self.assertEqual(exported["feature_schema"], FEATURE_SCHEMA_V4)
        except ImportError:
            self.skipTest("PyTorch not installed in this environment")


if __name__ == "__main__":
    unittest.main()
