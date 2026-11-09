"""Tests for Feature Schema v3, Policy v3, training evolution, and code review fixes."""
import copy
import math
import os
import signal
import tempfile
import unittest
from pathlib import Path

from engine.contracts import InputError, load_json
from engine.learning import (
    FEATURES,
    FEATURES_V2,
    FEATURES_V3,
    FEATURE_SCHEMA,
    FEATURE_SCHEMA_V2,
    FEATURE_SCHEMA_V3,
    Features,
    Policy,
    signed,
)
from engine.microsim import parse_sumo_emission_xml
from engine.moves import enumerate_moves
from engine.solver import proposal
from engine.validation import validate_solution


def make_v3_scenario():
    """Create a test scenario featuring M7/M8 variables (driver break, delivery loads, calibrated fuel, alternatives)."""
    s = load_json("tests/fixtures/two-stops.json")
    s = copy.deepcopy(s)
    # M7 mixed loads
    s["stops"][0]["delivery_mass_kg"] = 25.0
    s["stops"][0]["delivery_volume_m3"] = 0.2
    s["stops"][1]["delivery_mass_kg"] = 40.0
    s["stops"][1]["delivery_volume_m3"] = 0.3
    # M7 driver break
    s["driver_break"] = dict(enabled=True, threshold_s=3600.0, duration_s=900.0)
    # M7 heterogeneous capacities
    s["vehicle_capacities"] = {
        "van-1": dict(mass_kg=200.0, volume_m3=2.0),
        "van-2": dict(mass_kg=250.0, volume_m3=2.5),
    }
    # M7 alternative paths on leg
    s["legs"][0]["alternatives"] = [
        dict(
            id="alt-eco",
            travel_s=s["legs"][0]["travel_s"] + 10,
            segments=[dict(distance_m=1200.0, driving_category="slow")],
        )
    ]
    # M8 calibrated fuel model
    s["fuel_model"]["calibration"] = "microsim_calibrated"
    return s


def dummy_v3_model():
    """Generate a dummy signed v3 policy model."""
    layers = [
        dict(weight=[[0.01] * 46 for _ in range(64)], bias=[0.0] * 64),
        dict(weight=[[0.01] * 64 for _ in range(32)], bias=[0.0] * 32),
        dict(weight=[[0.01] * 32 for _ in range(2)], bias=[0.0] * 2),
    ]
    return signed(
        dict(
            schema_version=3,
            kind="learned_policy",
            architecture=[46, 64, 32, 2],
            feature_schema=FEATURE_SCHEMA_V3,
            metadata=dict(test=True),
            layers=layers,
        ),
        "model_hash",
    )


class FeatureSchemaV3Tests(unittest.TestCase):
    def test_schema_lengths_and_definitions(self):
        self.assertEqual(len(FEATURES), 30)
        self.assertEqual(len(FEATURES_V2), 38)
        self.assertEqual(len(FEATURES_V3), 46)
        self.assertEqual(FEATURE_SCHEMA["version"], 1)
        self.assertEqual(FEATURE_SCHEMA_V2["version"], 2)
        self.assertEqual(FEATURE_SCHEMA_V3["version"], 3)
        self.assertEqual(FEATURE_SCHEMA_V3["names"], FEATURES_V3)

    def test_features_v3_extraction_finite_and_expected_length(self):
        s = make_v3_scenario()
        sol = proposal(s, [["a"], ["b"]])
        val = validate_solution(s, sol)
        self.assertTrue(val["feasible"])

        ext_v1 = Features(s, [["a"], ["b"]], val, version=1)
        ext_v2 = Features(s, [["a"], ["b"]], val, version=2)
        ext_v3 = Features(s, [["a"], ["b"]], val, version=3)

        moves = list(enumerate_moves([["a"], ["b"]]))
        self.assertGreater(len(moves), 0)

        for m in moves:
            r1 = ext_v1.row(m)
            r2 = ext_v2.row(m)
            r3 = ext_v3.row(m)

            self.assertEqual(len(r1), 30)
            self.assertEqual(len(r2), 38)
            self.assertEqual(len(r3), 46)

            # v2 prefix matches v1
            self.assertEqual(r1[:30], r2[:30])
            # v3 prefix matches v2
            self.assertEqual(r2[:38], r3[:38])

            # Check that all elements are finite numbers
            self.assertTrue(all(math.isfinite(x) for x in r3))

            # Check domain feature constraints in v3 additions (index 38 to 45)
            # 38: source_delivery_mass_ratio >= 0
            self.assertGreaterEqual(r3[38], 0.0)
            # 39: dest_delivery_mass_ratio >= 0
            self.assertGreaterEqual(r3[39], 0.0)
            # 40: has_driver_break == 1.0 (since enabled)
            self.assertEqual(r3[40], 1.0)
            # 41: source_break_urgency in [0, 1]
            self.assertTrue(0.0 <= r3[41] <= 1.0)
            # 42: dest_break_urgency in [0, 1]
            self.assertTrue(0.0 <= r3[42] <= 1.0)
            # 43: has_path_alternatives in [0, 1]
            self.assertTrue(0.0 <= r3[43] <= 1.0)
            # 44: is_fuel_calibrated == 1.0
            self.assertEqual(r3[44], 1.0)
            # 45: traffic_slow_urban_ratio in [0, 1]
            self.assertTrue(0.0 <= r3[45] <= 1.0)

    def test_features_v3_default_values_when_features_disabled(self):
        s = load_json("tests/fixtures/two-stops.json")
        sol = proposal(s, [["a"], ["b"]])
        val = validate_solution(s, sol)
        ext = Features(s, [["a"], ["b"]], val, version=3)
        moves = list(enumerate_moves([["a"], ["b"]]))
        row = ext.row(moves[0])

        self.assertEqual(len(row), 46)
        # Without driver_break
        self.assertEqual(row[40], 0.0)
        self.assertEqual(row[41], 0.0)
        self.assertEqual(row[42], 0.0)
        # Without microsim calibration
        self.assertEqual(row[44], 0.0)


class PolicyV3Tests(unittest.TestCase):
    def test_policy_v3_initialization_and_prediction(self):
        model = dummy_v3_model()
        policy = Policy(model)
        self.assertEqual(policy.feature_version, 3)
        self.assertEqual(policy.input_dim, 46)

        # Predict on dummy features
        features = [[0.1] * 46, [0.5] * 46]
        scores = policy.predict(features)
        self.assertEqual(len(scores), 2)
        self.assertTrue(all(s > 0 for s in scores))

    def test_policy_order_integration(self):
        s = make_v3_scenario()
        sol = proposal(s, [["a"], ["b"]])
        val = validate_solution(s, sol)
        moves = list(enumerate_moves([["a"], ["b"]]))

        policy = Policy(dummy_v3_model())
        ordered = policy.order(s, [["a"], ["b"]], val, moves)
        self.assertEqual(len(ordered), len(moves))
        self.assertGreater(policy.inference_s, 0.0)

    def test_export_model_and_make_network(self):
        try:
            import torch
            from engine.learn_train import export_model, make_network

            net = make_network(version=3)
            self.assertEqual(net[0].in_features, 46)
            self.assertEqual(net[0].out_features, 64)
            self.assertEqual(net[2].in_features, 64)
            self.assertEqual(net[2].out_features, 32)
            self.assertEqual(net[4].in_features, 32)
            self.assertEqual(net[4].out_features, 2)

            exported = export_model(net, dict(epoch=1), version=3)
            self.assertEqual(exported["schema_version"], 3)
            self.assertEqual(exported["architecture"], [46, 64, 32, 2])
            self.assertEqual(exported["feature_schema"], FEATURE_SCHEMA_V3)

            # Check that exported policy loads and runs
            policy = Policy(exported)
            self.assertEqual(policy.feature_version, 3)
        except ImportError:
            self.skipTest("PyTorch not installed in current environment")


class CodeReviewFixTests(unittest.TestCase):
    def test_sumo_xml_namespace_handling(self):
        # SUMO XML with xmlns attribute
        xml_with_ns = """<?xml version="1.0" encoding="UTF-8"?>
        <emission-export xmlns="http://sumo.dlr.de/xsd/emission_file.xsd" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
            <timestep time="0.00">
                <vehicle id="van-1" speed="10.0" waiting="0.0" fuel="120.0" />
            </timestep>
            <timestep time="1.00">
                <vehicle id="van-1" speed="0.0" waiting="1.0" fuel="20.0" />
            </timestep>
        </emission-export>
        """
        parsed = parse_sumo_emission_xml(xml_with_ns)
        self.assertEqual(parsed["simulator"], "sumo-emission-xml-v1")
        self.assertEqual(parsed["timesteps"], 2)
        self.assertEqual(len(parsed["vehicles"]), 1)
        self.assertEqual(parsed["vehicles"][0]["vehicle_id"], "van-1")
        self.assertEqual(parsed["vehicles"][0]["travel_s"], 2)
        self.assertEqual(parsed["vehicles"][0]["waiting_s"], 1.0)
        self.assertEqual(parsed["vehicles"][0]["distance_m"], 10.0)

    def test_service_desktop_origins_opt_in(self):
        from fastapi.testclient import TestClient
        from engine.service import create_app

        with tempfile.TemporaryDirectory() as tmp:
            denied = TestClient(create_app(root=tmp, data_root="data"), base_url="http://localhost:8000")
            res = denied.get("/api/v1/health", headers={"Origin": "tauri://localhost"})
            self.assertEqual(res.status_code, 403)

            app = create_app(root=tmp, data_root="data", allowed_origins=("tauri://localhost",))
            client = TestClient(app, base_url="http://localhost:8000")
            res = client.options("/api/v1/health", headers={
                "Origin": "tauri://localhost", "Access-Control-Request-Method": "GET"})
            self.assertEqual(res.status_code, 204)
            self.assertEqual(res.headers.get("access-control-allow-origin"), "tauri://localhost")
            res = client.get("/api/v1/health", headers={"Origin": "tauri://localhost"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers.get("access-control-allow-origin"), "tauri://localhost")


if __name__ == "__main__":
    unittest.main()
