"""Milestone 6 tests: stochastic robustness, Feature/Policy v2, scale ladder (20/50/100 stops), and R&D API."""
import copy
from pathlib import Path
import tempfile
import unittest

from engine.city import CityRegistry
from engine.contracts import InputError
from engine.drafts import make_draft, prepare_draft
from engine.learning import FEATURE_SCHEMA, FEATURE_SCHEMA_V2, Features, Policy, signed
from engine.moves import enumerate_moves
from engine.solver import proposal, solve
from engine.stochastic import evaluate_stochastic
from engine.storage import write_json_atomic
from engine.validation import validate_solution
from engine.worker import Worker
from test_m2 import FakeRoads, HAS_SERVICE, TestClient, create_app, install_test_cities


def make_dummy_checkpoint(version=2):
    if version == 1:
        arch = [30, 32, 16, 2]
        schema = FEATURE_SCHEMA
        shapes = ((30, 32), (32, 16), (16, 2))
    else:
        arch = [38, 64, 32, 2]
        schema = FEATURE_SCHEMA_V2
        shapes = ((38, 64), (64, 32), (32, 2))
    return signed(
        dict(
            schema_version=version,
            kind='learned_policy',
            architecture=arch,
            feature_schema=schema,
            metadata=dict(training_device='cpu', seed=0, epoch=50, training_groups=120, validation_groups=40),
            layers=[dict(weight=[[0.01] * a for _ in range(b)], bias=[0.0] * b) for a, b in shapes],
        ),
        'model_hash',
    )


class StochasticRobustnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        install_test_cities(self.data)
        self.manifest = CityRegistry(self.data).get('bologna')
        self.draft = make_draft(self.manifest, seed=42, count=6, vehicles=2, service_mode='packages')
        self.scenario = prepare_draft(self.draft, self.manifest, FakeRoads())
        self.solution = proposal(self.scenario, self.scenario['generation']['witness_routes'])

    def test_zero_variability_matches_deterministic_validation(self):
        baseline = validate_solution(self.scenario, self.solution)
        report = evaluate_stochastic(self.scenario, self.solution, seed=7, samples=25, travel_cv=0.0, service_cv=0.0)
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['feasible_rate'], 1.0)
        self.assertEqual(report['window_compliance_rate'], 1.0)
        self.assertEqual(report['duration_cap_compliance_rate'], 1.0)
        self.assertAlmostEqual(report['metrics']['fleet_elapsed_s']['mean'], baseline['metrics']['fleet_elapsed_s'], places=4)
        self.assertAlmostEqual(report['metrics']['estimated_fuel_l']['p50'], baseline['metrics']['estimated_fuel_l'], places=4)
        self.assertEqual(report['metrics']['total_lateness_s']['max'], 0.0)

    def test_seeded_determinism_and_stress_violations(self):
        tight = copy.deepcopy(self.scenario)
        baseline = validate_solution(tight, self.solution)
        for v in baseline['vehicles']:
            for visit in v['visits']:
                for stop in tight['stops']:
                    if stop['id'] == visit['stop_id']:
                        stop['window_close_s'] = visit['service_start_s']
        tight['duration_cap_s'] = baseline['metrics']['completion_s']
        r1 = evaluate_stochastic(tight, self.solution, seed=99, samples=50, travel_cv=0.35, service_cv=0.35)
        r2 = evaluate_stochastic(tight, self.solution, seed=99, samples=50, travel_cv=0.35, service_cv=0.35)
        self.assertEqual(r1, r2)
        self.assertLess(r1['feasible_rate'], 1.0)
        self.assertGreater(r1['metrics']['total_lateness_s']['p95'], 0.0)
        self.assertTrue(any(sr['late_rate'] > 0 for sr in r1['stop_risks']))

    def test_invalid_inputs_rejected(self):
        infeasible = copy.deepcopy(self.solution)
        infeasible['routes'][0]['stop_ids'].pop()
        with self.assertRaises(InputError):
            evaluate_stochastic(self.scenario, infeasible)
        with self.assertRaises(InputError):
            evaluate_stochastic(self.scenario, self.solution, samples=0)
        with self.assertRaises(InputError):
            evaluate_stochastic(self.scenario, self.solution, travel_cv=-0.1)


class ScaleLadderAndPolicyV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        install_test_cities(self.data)
        self.manifest = CityRegistry(self.data).get('modena')

    def test_scale_ladder_50_and_100_stops_and_feature_schema_v2(self):
        for count, vans, mode, cap in ((20, 2, 'constant', None), (50, 4, 'packages', 14400), (100, 5, 'packages', None)):
            draft = make_draft(self.manifest, seed=100 + count, count=count, vehicles=vans, service_mode=mode, duration_cap_s=cap)
            scenario = prepare_draft(draft, self.manifest, FakeRoads())
            self.assertEqual(len(scenario['stops']), count)
            self.assertEqual(len(scenario['vehicle_ids']), vans)
            self.assertEqual(scenario['service']['mode'], mode)
            routes = scenario['generation']['witness_routes']
            val = validate_solution(scenario, proposal(scenario, routes))
            self.assertTrue(val['feasible'])
            f_v1 = Features(scenario, routes, val, version=1)
            f_v2 = Features(scenario, routes, val, version=2)
            first_move = next(enumerate_moves(routes))
            self.assertEqual(len(f_v1.row(first_move)), 30)
            self.assertEqual(len(f_v2.row(first_move)), 38)

    def test_policy_v2_checkpoint_solves_scenario(self):
        ckpt_path = Path(self.temp.name) / 'checkpoint-v2.json'
        write_json_atomic(ckpt_path, make_dummy_checkpoint(version=2))
        draft = make_draft(self.manifest, seed=7, count=8, vehicles=3, service_mode='packages', duration_cap_s=7200)
        scenario = prepare_draft(draft, self.manifest, FakeRoads())
        result = solve(scenario, seed=1, budget_s=2.0, restarts=1, selection_policy='learned', checkpoint=str(ckpt_path))
        self.assertTrue(result['validation']['feasible'])
        self.assertEqual(result['learned']['inference'], 'numpy-fp32')


@unittest.skipUnless(HAS_SERVICE, 'install service/test dependencies from requirements.lock')
class RnDServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'runs'
        self.data = Path(self.temp.name) / 'data'
        install_test_cities(self.data)
        write_json_atomic(self.root / 'm4-imports' / 'v1-run' / 'checkpoint.json', make_dummy_checkpoint(version=1))
        write_json_atomic(self.root / 'm6-models' / 'v2-run' / 'checkpoint.json', make_dummy_checkpoint(version=2))

    def worker(self):
        w = Worker(self.root, self.data, FakeRoads).acquire()
        self.addCleanup(w.close)
        return w

    def test_rnd_models_stochastic_and_scale_campaign_endpoints(self):
        with TestClient(create_app(self.root, self.data), base_url='http://localhost:8000') as client:
            models = client.get('/api/v1/rnd/models').json()
            self.assertEqual({m['schema_version'] for m in models}, {1, 2})

            d = client.post('/api/v1/drafts', json=dict(city_id='bologna', count=4)).json()
            prep = client.post('/api/v1/jobs', json=dict(kind='prepare', draft_id=d['id'], revision=d['revision'])).json()
            w = self.worker()
            w.execute_one()

            solve_job = client.post(
                '/api/v1/jobs',
                json=dict(kind='solve', draft_id=d['id'], revision=d['revision'], prepared_job_id=prep['id'], budget_s=2, restarts=1, policy='learned'),
            ).json()
            w.execute_one()

            stoch = client.post(f"/api/v1/jobs/{solve_job['id']}/stochastic", json=dict(seed=11, samples=20, travel_cv=0.1, service_cv=0.15))
            self.assertEqual(stoch.status_code, 200, stoch.text)
            stoch_data = stoch.json()
            self.assertEqual(len(stoch_data['stop_risks']), 4)
            self.assertIn('feasible_rate', stoch_data)

            camp = client.post(
                '/api/v1/rnd/campaigns',
                json=dict(
                    prepared_job_id=prep['id'],
                    vehicles=3,
                    service_mode='packages',
                    methods=['ordered', 'learned'],
                    budgets=[5],
                    weights=[1.0, 0.5],
                    repetitions=1,
                ),
            )
            self.assertEqual(camp.status_code, 202, camp.text)
            camp_id = camp.json()['id']
            w.execute_one()

            report = client.get(f'/api/v1/experiments/{camp_id}/report')
            self.assertEqual(report.status_code, 200, report.text)
            rep_data = report.json()
            self.assertEqual(rep_data['completed'], 4)
            self.assertEqual(rep_data['total'], 4)
            self.assertTrue(rep_data['campaign_complete'])
