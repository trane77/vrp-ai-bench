"""Milestone 7 tests: alternative road paths and operational constraints."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.contracts import InputError, load_json, validate_scenario
from engine.routing import OSRM
from engine.solver import proposal, solve
from engine.validation import validate_solution
from engine.worker import Worker
from test_m2 import TestClient, create_app, install_test_cities


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "two-stops.json"


class FakeOSRMWithAlternatives:
    """Deterministic local OSRM double returning two distinct road paths per leg."""

    def snap(self, point):
        return dict(requested=list(point), snapped=list(point), snap_distance_m=1.5)

    def route(self, source, target, dataset_hash):
        a, b = source["snapped"], target["snapped"]
        primary_seg = [dict(distance_m=2400.0, driving_category="fast")]
        eco_seg = [dict(distance_m=1600.0, driving_category="urban")]
        return dict(
            source=source["id"],
            target=target["id"],
            dataset_hash=dataset_hash,
            reachable=True,
            travel_s=120,
            raw_duration_s=120.0,
            segments=primary_seg,
            geometry=[list(a), list(b)],
            annotations=dict(distance_m=[2400.0], duration_s=[120.0]),
            alternatives=[
                dict(
                    id="alt-0",
                    travel_s=120,
                    distance_m=2400.0,
                    raw_duration_s=120.0,
                    segments=primary_seg,
                    geometry=[list(a), list(b)],
                ),
                dict(
                    id="alt-1",
                    travel_s=180,
                    distance_m=1600.0,
                    raw_duration_s=180.0,
                    segments=eco_seg,
                    geometry=[list(a), list(b)],
                ),
            ],
        )


class M7Tests(unittest.TestCase):
    def setUp(self):
        self.base = load_json(FIXTURE)

    def test_osrm_adapter_parses_and_deduplicates_alternatives(self):
        osrm = OSRM("http://127.0.0.1:5000")
        canned = {
            "code": "Ok",
            "routes": [
                {
                    "duration": 100.0,
                    "geometry": {"coordinates": [[11.34, 44.49], [11.35, 44.50]]},
                    "legs": [{"annotation": {"distance": [1800.0], "duration": [100.0]}}],
                },
                # Duplicate of route 0 -> should be deduplicated
                {
                    "duration": 100.0,
                    "geometry": {"coordinates": [[11.34, 44.49], [11.35, 44.50]]},
                    "legs": [{"annotation": {"distance": [1800.0], "duration": [100.0]}}],
                },
                # Distinct alternative (slower, shorter)
                {
                    "duration": 140.0,
                    "geometry": {"coordinates": [[11.34, 44.49], [11.345, 44.495], [11.35, 44.50]]},
                    "legs": [{"annotation": {"distance": [1300.0], "duration": [140.0]}}],
                },
            ],
        }
        with patch.object(osrm, "request", return_value=canned) as req_mock:
            src = dict(id="depot", snapped=[11.34, 44.49])
            dst = dict(id="stop-1", snapped=[11.35, 44.50])
            leg = osrm.route(src, dst, "hash123")
            self.assertIn("alternatives=3", req_mock.call_args[0][0])
            self.assertIn("alternatives", leg)
            self.assertEqual(len(leg["alternatives"]), 2)
            self.assertEqual(leg["alternatives"][0]["id"], "alt-0")
            self.assertEqual(leg["alternatives"][1]["id"], "alt-1")
            self.assertEqual(leg["alternatives"][0]["travel_s"], 100)
            self.assertEqual(leg["alternatives"][1]["travel_s"], 140)

    def test_alternative_path_selection_responds_to_objective_and_windows(self):
        s = copy.deepcopy(self.base)
        s["provenance"]["path_policy"] = "osrm_alternatives_evaluated"
        s["path_optimization"] = True
        for leg in s["legs"]:
            if leg["reachable"]:
                fast_dist = sum(seg["distance_m"] for seg in leg["segments"]) * 1.5
                eco_dist = sum(seg["distance_m"] for seg in leg["segments"]) * 0.7
                leg["alternatives"] = [
                    dict(
                        id="alt-0",
                        travel_s=leg["travel_s"],
                        distance_m=fast_dist,
                        segments=[dict(distance_m=fast_dist, driving_category="urban")],
                    ),
                    dict(
                        id="alt-1",
                        travel_s=leg["travel_s"] + 120,
                        distance_m=eco_dist,
                        segments=[dict(distance_m=eco_dist, driving_category="urban")],
                    ),
                ]

        # 1) Time-only objective selects faster alt-0
        s_time = copy.deepcopy(s)
        s_time["objective"]["time_weight"] = 1.0
        s_time["objective"]["fuel_weight"] = 0.0
        run_time = solve(s_time, seed=1, budget_s=1.0, restarts=2)
        self.assertEqual(run_time["feasibility"], "feasible")
        legs_time = [l for v in run_time["validation"]["vehicles"] for l in v["legs"]]
        self.assertTrue(all(l["path_id"] == "alt-0" for l in legs_time))

        # 2) Fuel-only objective selects shorter eco alt-1 when windows allow
        s_fuel = copy.deepcopy(s)
        for stop in s_fuel["stops"]:
            stop["window_close_s"] = 10000
        s_fuel["duration_cap_s"] = 10000
        s_fuel["objective"]["time_weight"] = 0.0
        s_fuel["objective"]["fuel_weight"] = 1.0
        run_fuel = solve(s_fuel, seed=1, budget_s=1.0, restarts=2)
        self.assertEqual(run_fuel["feasibility"], "feasible")
        legs_fuel = [l for v in run_fuel["validation"]["vehicles"] for l in v["legs"]]
        self.assertTrue(all(l["path_id"] == "alt-1" for l in legs_fuel))
        self.assertLess(
            run_fuel["validation"]["metrics"]["estimated_fuel_l"],
            run_time["validation"]["metrics"]["estimated_fuel_l"],
        )

        # 3) Tight time window on first stop forces alt-0 even under fuel-only objective
        s_tight = copy.deepcopy(s_fuel)
        first_stop_id = run_fuel["solution"]["routes"][0]["stop_ids"][0]
        for stop in s_tight["stops"]:
            if stop["id"] == first_stop_id:
                # Reachable via alt-0, but late via alt-1 (+120s)
                leg_d_s = next(l for l in s_tight["legs"] if l["source"] == s_tight["depot_id"] and l["target"] == first_stop_id)
                stop["window_open_s"] = s_tight["start_s"]
                stop["window_close_s"] = s_tight["start_s"] + leg_d_s["travel_s"] + 10
        rep_tight = validate_solution(validate_scenario(s_tight), proposal(s_tight, [r["stop_ids"] for r in run_fuel["solution"]["routes"]]))
        self.assertTrue(rep_tight["feasible"])
        self.assertEqual(rep_tight["vehicles"][0]["legs"][0]["path_id"], "alt-0")
        self.assertEqual(rep_tight["vehicles"][0]["legs"][1]["path_id"], "alt-1")

        # 4) Explicit unknown path_id is rejected by independent validator
        bad_sol = copy.deepcopy(run_fuel["solution"])
        bad_sol["routes"][0]["path_ids"] = ["unknown-alt"] * len(legs_fuel)
        rep_bad = validate_solution(validate_scenario(s_fuel), bad_sol)
        self.assertFalse(rep_bad["feasible"])
        self.assertTrue(any("unknown path_id" in err for err in rep_bad["violations"]))

    def test_heterogeneous_vehicle_capacities(self):
        s = copy.deepcopy(self.base)
        s["vehicle_ids"] = ["van-small", "van-large"]
        s["capacity"] = dict(mass_kg=50, volume_m3=5)
        s["vehicle_capacities"] = {
            "van-small": dict(mass_kg=5, volume_m3=1),
            "van-large": dict(mass_kg=50, volume_m3=5),
        }
        for stop in s["stops"]:
            stop["mass_kg"] = 10
        s = validate_scenario(s)
        stop_ids = [stop["id"] for stop in s["stops"]]

        # Assigning all stops to van-small exceeds its 5 kg capacity
        rep_small = validate_solution(
            s,
            dict(
                schema_version=1,
                scenario_id=s["id"],
                routes=[
                    dict(vehicle_id="van-small", stop_ids=stop_ids),
                    dict(vehicle_id="van-large", stop_ids=[]),
                ],
            ),
        )
        self.assertFalse(rep_small["feasible"])
        self.assertTrue(any("van-small: capacity exceeded" in v for v in rep_small["violations"]))

        # Assigning all stops to van-large succeeds
        rep_large = validate_solution(
            s,
            dict(
                schema_version=1,
                scenario_id=s["id"],
                routes=[
                    dict(vehicle_id="van-small", stop_ids=[]),
                    dict(vehicle_id="van-large", stop_ids=stop_ids),
                ],
            ),
        )
        self.assertTrue(rep_large["feasible"])

    def test_mandatory_driver_break_accounting_and_feasibility(self):
        s = copy.deepcopy(self.base)
        for stop in s["stops"]:
            stop["window_close_s"] = 10000
        s["duration_cap_s"] = 10000
        s["driver_break"] = dict(enabled=True, threshold_s=30, duration_s=600)
        s = validate_scenario(s)
        stop_ids = [stop["id"] for stop in s["stops"]]
        sol = proposal(s, [stop_ids])
        rep = validate_solution(s, sol)
        self.assertTrue(rep["feasible"])
        breaks = [v.get("break_s", 0) for v in rep["vehicles"][0]["visits"]]
        self.assertIn(600, breaks)
        self.assertGreaterEqual(rep["metrics"]["waiting_s"], 600)

        # Tight duration cap fails when mandatory 600s break is triggered
        s_tight = copy.deepcopy(s)
        s_tight["duration_cap_s"] = rep["metrics"]["completion_s"] - 100
        rep_tight = validate_solution(validate_scenario(s_tight), sol)
        self.assertFalse(rep_tight["feasible"])
        self.assertTrue(any("duration cap exceeded" in v for v in rep_tight["violations"]))

    def test_mixed_pickup_and_delivery_loads(self):
        s = copy.deepcopy(self.base)
        for stop in s["stops"]:
            stop["window_close_s"] = 10000
            stop["mass_kg"] = 2.0  # pickup at stop
            stop["delivery_mass_kg"] = 15.0  # loaded at depot, delivered at stop
            stop["delivery_volume_m3"] = 0.2
        s["duration_cap_s"] = 10000
        s["capacity"] = dict(mass_kg=40.0, volume_m3=2.0)
        s = validate_scenario(s)
        stop_ids = [stop["id"] for stop in s["stops"]]
        sol = proposal(s, [stop_ids])
        rep = validate_solution(s, sol)
        self.assertTrue(rep["feasible"])
        # Outward leg from depot carries total delivery mass (15 * len(stops))
        first_leg = rep["vehicles"][0]["legs"][0]
        self.assertAlmostEqual(first_leg["load_kg"], 15.0 * len(stop_ids))
        # After first stop, load decreases by 15 (delivered) and increases by 2 (picked up)
        first_visit = rep["vehicles"][0]["visits"][0]
        self.assertAlmostEqual(first_visit["load_kg"], 15.0 * (len(stop_ids) - 1) + 2.0)

        # If depot departure delivery load exceeds vehicle capacity, validator rejects it
        s_over = copy.deepcopy(s)
        s_over["capacity"]["mass_kg"] = 20.0
        rep_over = validate_solution(validate_scenario(s_over), sol)
        self.assertFalse(rep_over["feasible"])
        self.assertTrue(any("capacity exceeded at depot departure" in v for v in rep_over["violations"]))

    def test_rnd_path_comparison_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            data = Path(tmp) / "data"
            install_test_cities(data)
            app = create_app(root=root, data_root=data)
            client = TestClient(app, base_url="http://localhost:8000")
            draft_res = client.post("/api/v1/drafts", json=dict(city_id="bologna", seed=7, count=6))
            self.assertEqual(draft_res.status_code, 201)
            draft_doc = draft_res.json()

            # Enable M7 constraints on the draft and save
            draft_data = draft_doc["data"]
            draft_data["parameters"]["path_optimization"] = True
            draft_data["parameters"]["driver_break"] = dict(enabled=True, threshold_s=3600, duration_s=300)
            draft_data["stops"][0]["delivery_mass_kg"] = 5.0
            put_res = client.put(
                f"/api/v1/drafts/{draft_doc['id']}",
                json=dict(revision=draft_doc["revision"], data=draft_data),
            )
            self.assertEqual(put_res.status_code, 200)
            updated_doc = put_res.json()

            prep_res = client.post(
                "/api/v1/jobs",
                json=dict(kind="prepare", draft_id=updated_doc["id"], revision=updated_doc["revision"]),
            )
            self.assertEqual(prep_res.status_code, 202)
            prep_id = prep_res.json()["id"]

            worker = Worker(
                root=root,
                data_root=data,
                provider_factory=lambda cancelled=None: FakeOSRMWithAlternatives(),
            ).acquire()
            try:
                self.assertTrue(worker.execute_one())
            finally:
                worker.close()

            # Verify editor round-trip preserves M7 fields
            ed_res = client.get(f"/api/v1/jobs/{prep_id}/editor")
            self.assertEqual(ed_res.status_code, 200)
            ed_data = ed_res.json()
            self.assertTrue(ed_data["parameters"].get("path_optimization"))
            self.assertTrue(ed_data["parameters"]["driver_break"]["enabled"])
            self.assertEqual(ed_data["stops"][0]["delivery_mass_kg"], 5.0)

            # Run R&D path comparison
            cmp_res = client.post(
                "/api/v1/rnd/path-comparison",
                json=dict(prepared_job_id=prep_id, seed=7, budget_s=1.0, max_evaluations=200),
            )
            self.assertEqual(cmp_res.status_code, 200)
            report = cmp_res.json()
            self.assertEqual(report["city_id"], "bologna")
            self.assertGreater(report["legs_with_alternatives"], 0)
            self.assertEqual(len(report["comparisons"]), 3)
            # For fuel_weight=1.0 (time_weight=0.0), multi_path selects eco alternatives and reduces fuel
            fuel_row = next(r for r in report["comparisons"] if r["time_weight"] == 0.0)
            self.assertTrue(fuel_row["single_path"]["feasible"])
            self.assertTrue(fuel_row["multi_path"]["feasible"])
            self.assertGreater(fuel_row["multi_path"]["alt_paths_selected"], 0)
            self.assertLess(
                fuel_row["multi_path"]["estimated_fuel_l"],
                fuel_row["single_path"]["estimated_fuel_l"],
            )


if __name__ == "__main__":
    unittest.main()
