"""Regression tests for the ultrareview findings (drafts, validator path policy, microsim, SUMO units, witness breaks)."""
import copy
import unittest
from pathlib import Path

from engine.contracts import InputError, load_json, validate_scenario
from engine.drafts import MAX_PREPARED_STOPS, assign_witness_windows, make_draft, prepare_draft
from engine.microsim import evaluate_microsim, parse_sumo_emission_xml
from engine.solver import proposal
from engine.validation import validate_solution
from test_m2 import FakeRoads

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "two-stops.json"
MANIFEST = dict(city_id="bologna", center=[11.3426, 44.4949], radius_m=5000, manifest_hash="h")


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.s = load_json(FIXTURE)
        self.sol = proposal(self.s, [[st["id"]] for st in self.s["stops"]][:2] + [[]] * (len(self.s["vehicle_ids"]) - 2))

    def test_microsim_runs_on_calibrated_scenario(self):
        s = copy.deepcopy(self.s)
        s["fuel_model"].update(calibration="microsim_calibrated", label="microsim-calibrated estimate")
        evaluate_microsim(validate_scenario(s), self.sol)

    def test_validator_rejects_path_ids_under_fixed_policy(self):
        sol = copy.deepcopy(self.sol)
        route = next(r for r in sol["routes"] if r["stop_ids"])
        route["path_ids"] = ["default"] * (len(route["stop_ids"]) + 1)
        report = validate_solution(validate_scenario(self.s), sol)
        self.assertFalse(report["feasible"])
        self.assertTrue(any("fixed_directed_paths" in v for v in report["violations"]))

    def test_large_draft_stays_inside_service_area_and_prepare_fails_fast(self):
        from engine.drafts import validate_draft
        draft = make_draft(MANIFEST, seed=1, count=500)
        validate_draft(draft, MANIFEST)
        with self.assertRaisesRegex(InputError, "at most %d stops" % MAX_PREPARED_STOPS):
            prepare_draft(draft, MANIFEST, FakeRoads())

    def test_sumo_unit_is_decided_per_document(self):
        xml = """<emission-export><timestep time="0"><vehicle id="a" speed="1" fuel="500"/></timestep>
                 <timestep time="1"><vehicle id="a" speed="1" fuel="10"/></timestep></emission-export>"""
        parsed = parse_sumo_emission_xml(xml)
        self.assertEqual(parsed["fuel_unit"], "mg/s")
        self.assertAlmostEqual(parsed["vehicles"][0]["fuel_l"], 510 / 832_000, places=5)

    def test_witness_windows_include_driver_break(self):
        s = copy.deepcopy(self.s)
        routes = [[s["stops"][0]["id"], s["stops"][1]["id"]], []]
        s["driver_break"] = dict(enabled=True, threshold_s=1, duration_s=900)
        assign_witness_windows(s, routes)
        gap = s["stops"][1]["window_close_s"] - s["stops"][0]["window_close_s"]
        s["driver_break"]["enabled"] = False
        assign_witness_windows(s, routes)
        self.assertEqual(gap - (s["stops"][1]["window_close_s"] - s["stops"][0]["window_close_s"]), 900)


if __name__ == "__main__":
    unittest.main()
