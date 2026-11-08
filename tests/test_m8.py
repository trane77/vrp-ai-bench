"""Milestone 8 tests: traffic microsimulation, fuel-model calibration, SUMO XML ingestion, and label separation."""
import copy
from pathlib import Path
import random
import tempfile
import unittest

from engine.contracts import InputError, load_json, validate_scenario
from engine.microsim import (
    calibrate_fuel_model,
    evaluate_microsim,
    parse_sumo_emission_xml,
    simulate_segment_microsim,
)
from engine.solver import proposal
from engine.validation import validate_solution
from engine.worker import Worker
from test_m2 import FakeRoads, TestClient, create_app, install_test_cities


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "two-stops.json"


class M8Tests(unittest.TestCase):
    def setUp(self):
        self.base = load_json(FIXTURE)

    def test_longitudinal_microsim_physics_invariants(self):
        # 1) Loaded van consumes strictly more fuel than empty van on identical road segment
        sim_empty = simulate_segment_microsim(
            2000.0,
            "urban",
            0.0,
            rng=random.Random(10),
            congestion_factor=1.1,
            signal_delay_scale=1.0,
        )
        sim_loaded = simulate_segment_microsim(
            2000.0,
            "urban",
            800.0,
            rng=random.Random(10),
            congestion_factor=1.1,
            signal_delay_scale=1.0,
        )
        self.assertGreater(sim_loaded["fuel_l"], sim_empty["fuel_l"])

        # 2) Signal delay scale = 0 produces zero signal wait, whereas scale = 2 produces positive signal wait
        sim_no_sig = simulate_segment_microsim(
            2000.0,
            "slow",
            100.0,
            rng=random.Random(20),
            congestion_factor=1.0,
            signal_delay_scale=0.0,
        )
        sim_with_sig = simulate_segment_microsim(
            2000.0,
            "slow",
            100.0,
            rng=random.Random(20),
            congestion_factor=1.0,
            signal_delay_scale=2.0,
        )
        self.assertEqual(sim_no_sig["signal_delay_s"], 0.0)
        self.assertGreater(sim_with_sig["signal_delay_s"], 0.0)
        self.assertGreater(sim_with_sig["travel_s"], sim_no_sig["travel_s"])

    def test_fuel_calibration_and_strict_label_separation(self):
        s = copy.deepcopy(self.base)
        sol = proposal(s, [["a", "b"], []])
        rep_uncal = validate_solution(validate_scenario(s), sol)
        self.assertTrue(rep_uncal["feasible"])
        self.assertEqual(rep_uncal["fuel_label"], "experimental estimate (uncalibrated)")

        # Calibrate against microsimulator
        cal = calibrate_fuel_model(s, seed=42, congestion_factor=1.15, signal_delay_scale=1.0)
        fm = cal["calibrated_fuel_model"]
        self.assertEqual(fm["calibration"], "microsim_calibrated")
        self.assertEqual(fm["label"], "microsim-calibrated estimate")
        self.assertIn("calibration_metadata", fm)
        self.assertIn("validity_bounds", fm["calibration_metadata"])

        # Scenario with calibrated model validates and reports microsim_calibrated label
        s_cal = copy.deepcopy(s)
        s_cal["fuel_model"] = fm
        rep_cal = validate_solution(validate_scenario(s_cal), sol)
        self.assertTrue(rep_cal["feasible"])
        self.assertEqual(
            rep_cal["fuel_label"],
            "microsim-calibrated estimate (microsim_calibrated)",
        )

        # Mismatched calibration and label is rejected by contract check
        s_bad = copy.deepcopy(s_cal)
        s_bad["fuel_model"]["label"] = "experimental estimate"
        with self.assertRaises(InputError):
            validate_scenario(s_bad)

    def test_evaluate_microsim_reduces_fuel_error_and_reports_drift(self):
        s = copy.deepcopy(self.base)
        for stop in s["stops"]:
            stop["window_close_s"] = 500
        s["duration_cap_s"] = 1000
        sol = proposal(s, [["a", "b"], []])
        report = evaluate_microsim(
            s,
            sol,
            seed=42,
            congestion_factor=1.20,
            signal_delay_scale=1.0,
        )
        self.assertEqual(report["simulator"]["name"], "longitudinal-tractive-v1")
        self.assertGreater(report["timing_drift"]["microsim_travel_s"], 0.0)
        self.assertEqual(len(report["stop_reports"]), 2)
        self.assertEqual(len(report["vehicle_reports"]), 1)

        fc = report["fuel_comparison"]
        self.assertIn("uncalibrated", fc["uncalibrated_parametric"]["label"])
        self.assertIn("microsim_calibrated", fc["calibrated_parametric"]["label"])
        self.assertGreater(fc["microsim_reference"]["co2_kg"], 0.0)
        # Calibrated parametric model is closer to the physical microsimulated reference than uncalibrated defaults
        self.assertLess(
            fc["calibrated_parametric"]["abs_error_pct"],
            fc["uncalibrated_parametric"]["abs_error_pct"],
        )

    def test_parse_sumo_emission_xml(self):
        sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <emission-export>
            <timestep time="0.00">
                <vehicle id="van-1" speed="0.00" waiting="1.00" fuel="0.28"/>
            </timestep>
            <timestep time="1.00">
                <vehicle id="van-1" speed="10.50" waiting="0.00" fuel="1.85"/>
                <vehicle id="van-2" speed="8.00" waiting="0.00" fuel="1.40"/>
            </timestep>
            <timestep time="2.00">
                <vehicle id="van-1" speed="12.00" waiting="0.00" fuel="2.10"/>
                <vehicle id="van-2" speed="0.00" waiting="1.00" fuel="0.28"/>
            </timestep>
        </emission-export>
        """
        parsed = parse_sumo_emission_xml(sample_xml)
        self.assertEqual(parsed["simulator"], "sumo-emission-xml-v1")
        self.assertEqual(parsed["timesteps"], 3)
        self.assertEqual(len(parsed["vehicles"]), 2)
        v1 = next(v for v in parsed["vehicles"] if v["vehicle_id"] == "van-1")
        self.assertEqual(v1["travel_s"], 3)
        self.assertEqual(v1["waiting_s"], 1.0)
        self.assertAlmostEqual(v1["distance_m"], 22.5)
        self.assertGreater(parsed["totals"]["fuel_l"], 0.0)
        self.assertGreater(parsed["totals"]["co2_kg"], 0.0)

    def test_microsim_service_endpoint_and_calibrated_draft_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            data = Path(tmp) / "data"
            install_test_cities(data)
            app = create_app(root=root, data_root=data)
            client = TestClient(app, base_url="http://localhost:8000")

            draft_res = client.post("/api/v1/drafts", json=dict(city_id="bologna", seed=9, count=5))
            self.assertEqual(draft_res.status_code, 201)
            d = draft_res.json()

            prep = client.post(
                "/api/v1/jobs",
                json=dict(kind="prepare", draft_id=d["id"], revision=d["revision"]),
            ).json()

            worker = Worker(root=root, data_root=data, provider_factory=FakeRoads).acquire()
            try:
                self.assertTrue(worker.execute_one())
                solve_job = client.post(
                    "/api/v1/jobs",
                    json=dict(
                        kind="solve",
                        draft_id=d["id"],
                        revision=d["revision"],
                        prepared_job_id=prep["id"],
                        budget_s=1.5,
                        restarts=1,
                    ),
                ).json()
                self.assertTrue(worker.execute_one())
            finally:
                worker.close()

            ms_res = client.post(
                f"/api/v1/jobs/{solve_job['id']}/microsim",
                json=dict(seed=42, congestion_factor=1.15, signal_delay_scale=1.0),
            )
            self.assertEqual(ms_res.status_code, 200, ms_res.text)
            ms_data = ms_res.json()
            self.assertEqual(ms_data["simulator"]["name"], "longitudinal-tractive-v1")
            cal_fm = ms_data["calibration"]["calibrated_fuel_model"]
            self.assertEqual(cal_fm["calibration"], "microsim_calibrated")

            # Applying calibrated fuel model back to the draft succeeds
            d_data = copy.deepcopy(d["data"])
            d_data["parameters"]["fuel_model"] = cal_fm
            put_res = client.put(
                f"/api/v1/drafts/{d['id']}",
                json=dict(revision=d["revision"], data=d_data),
            )
            self.assertEqual(put_res.status_code, 200, put_res.text)
            self.assertEqual(
                put_res.json()["data"]["parameters"]["fuel_model"]["calibration"],
                "microsim_calibrated",
            )


if __name__ == "__main__":
    unittest.main()
