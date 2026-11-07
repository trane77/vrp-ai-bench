import copy
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from engine.contracts import (InputError, RUN, SCHEMAS, check, load_json,
                              validate_scenario)
from engine.solver import neighbors, proposal, scenario_hash, solve
from engine.storage import write_json_atomic
from engine.validation import objective_score, validate_solution

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/two-stops.json'


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.s = validate_scenario(load_json(FIXTURE))
        self.p = proposal(self.s, [['a', 'b'], []])

    def report(self):
        return validate_solution(validate_scenario(self.s), self.p)

    def test_hand_calculated_schedule_and_loaded_return(self):
        r = self.report()
        self.assertTrue(r['feasible'], r['violations'])
        m = r['metrics']
        self.assertEqual([m[k] for k in ('fleet_elapsed_s','completion_s','travel_s',
                                         'waiting_s','service_s','distance_m','used_vans','available_vans')],
                         [90,90,60,10,20,6000,1,2])
        self.assertAlmostEqual(m['estimated_fuel_l'], .74)
        v = r['vehicles'][0]
        self.assertEqual([x['load_kg'] for x in v['legs']], [0,10,30])
        self.assertEqual([x['service_start_s'] for x in v['visits']], [20,50])
        self.assertEqual(v['visits'][0]['departure_s'], 30)  # after window close
        self.assertEqual(r['vehicles'][1]['metrics']['fleet_elapsed_s'], 0)
        self.assertEqual(r['vehicles'][1]['return_s'], 0)

    def test_package_service(self):
        self.s['service']['mode'] = 'packages'
        r = self.report()
        self.assertEqual(r['metrics']['service_s'],17)
        self.assertEqual(r['metrics']['fleet_elapsed_s'],87)
        self.assertAlmostEqual(r['metrics']['estimated_fuel_l'],.737)

    def test_start_offset(self):
        self.s['start_s'] = 100
        for stop in self.s['stops']:
            stop['window_open_s'] += 100
            stop['window_close_s'] += 100
        r = self.report()
        self.assertEqual(r['metrics']['completion_s'],90)
        self.assertEqual(r['vehicles'][0]['return_s'],190)
        self.assertEqual(r['vehicles'][1]['return_s'],100)

    def test_window_and_return_boundaries(self):
        self.s['duration_cap_s'] = 89
        self.assertIn('duration cap', ' '.join(self.report()['violations']))
        self.s['duration_cap_s'] = None
        self.s['stops'][1]['window_close_s'] = 49
        self.s['stops'][1]['window_open_s'] = 49
        self.assertIn('late service start', ' '.join(self.report()['violations']))

    def test_capacity_mass_and_volume(self):
        self.s['capacity']['volume_m3']=.3
        self.s['capacity']['mass_kg']=30
        self.assertTrue(self.report()['feasible'])
        for field, cap in [('mass_kg',29),('volume_m3',.29)]:
            with self.subTest(field=field):
                s = copy.deepcopy(self.s)
                s['capacity'][field] = cap
                report = validate_solution(s,self.p)
                self.assertIn('capacity exceeded after b',' '.join(report['violations']))

    def test_idle_off_and_segment_categories(self):
        self.s['fuel_model']['idle_policy']='off'
        self.s['fuel_model']['idle_l_per_s']=0
        self.s['fuel_model']['categories']['fast'] = dict(base_l_per_km=.2,load_l_per_km_kg=.002)
        self.s['legs'][2]['segments'] = [dict(distance_m=1000,driving_category='urban'),
                                         dict(distance_m=2000,driving_category='fast')]
        self.assertAlmostEqual(self.report()['metrics']['estimated_fuel_l'],.97)

    def test_unreachable_return_and_asymmetry(self):
        self.s['legs'][2].update(reachable=False,travel_s=0,segments=[])
        self.assertIn('unreachable leg b->depot',' '.join(self.report()['violations']))
        s = validate_scenario(load_json(FIXTURE))
        r = validate_solution(s,proposal(s,[['b','a'],[]]))
        self.assertFalse(r['feasible'])
        self.assertEqual(r['metrics']['travel_s'],150)

    def test_distance_control_must_be_labeled(self):
        fuel=self.s['fuel_model']
        fuel['categories']['urban']['load_l_per_km_kg']=0
        with self.assertRaises(InputError):
            validate_scenario(self.s)
        fuel.update(kind='distance_equivalent_control',
                    label='distance-equivalent control estimate',
                    idle_policy='off',idle_l_per_s=0)
        report=self.report()
        self.assertAlmostEqual(report['metrics']['estimated_fuel_l'],.6)
        self.assertIn('distance-equivalent control',report['fuel_label'])

    def test_bad_visits_and_vehicles(self):
        for routes, text in [([['a'],[]],'missing stop'),
                             ([['a','a','b'],[]],'duplicate stop'),
                             ([['z','b'],[]],'unknown stop')]:
            with self.subTest(text=text):
                r=validate_solution(self.s,proposal(self.s,routes))
                self.assertFalse(r['feasible'])
                self.assertIn(text,' '.join(r['violations']))
        self.p['routes'][1]['vehicle_id']='alien'
        self.assertIn('unknown vehicle',' '.join(self.report()['violations']))
        self.p['routes'][1]['vehicle_id']='van-1'
        self.assertIn('duplicate vehicle',' '.join(self.report()['violations']))

    def test_objective_weights_and_scales(self):
        r=self.report()
        self.assertAlmostEqual(objective_score(self.s,r), .82)
        self.s['objective']['time_weight']=0
        self.assertAlmostEqual(objective_score(self.s,r), .37)
        self.s['objective']['fuel_weight']=0
        with self.assertRaises(InputError):
            validate_scenario(self.s)

    def test_bad_numeric_inputs(self):
        for value in [-1, True, float('nan'),float('inf'), '10']:
            with self.subTest(value=value):
                s=copy.deepcopy(self.s)
                s['stops'][0]['mass_kg']=value
                with self.assertRaises(InputError):
                    validate_scenario(s)
        self.s['legs'][0]['travel_s']=10.5
        with self.assertRaises(InputError):
            validate_scenario(self.s)

    def test_invalid_contracts(self):
        changes=[lambda s:s.update(schema_version=2),
                 lambda s:s.update(timezone='Unknown/Place'),
                 lambda s:s.update(reference_instant='2026-01-01T00:00:00'),
                 lambda s:s.update(unknown=True),
                 lambda s:s['stops'][1].update(id='a'),
                 lambda s:s['stops'][1].update(window_close_s=0),
                 lambda s:s['legs'].pop(),
                 lambda s:s['legs'].append(s['legs'][0]),
                 lambda s:s['legs'][0]['segments'][0].update(driving_category='missing'),
                 lambda s:s.update(vehicle_ids=['v']*6),
                 lambda s:s.update(vehicle_ids=[[]]),
                 lambda s:s['objective'].update(time_scale_s=0)]
        for change in changes:
            with self.subTest(change=change):
                s=copy.deepcopy(self.s)
                change(s)
                with self.assertRaises(InputError):
                    validate_scenario(s)
        self.p['scenario_id']='other'
        with self.assertRaises(InputError):
            self.report()

    def test_empty_scenario_and_unused_vehicles(self):
        self.s['stops']=[]
        self.s['legs']=[]
        r=validate_solution(validate_scenario(self.s),proposal(self.s,[[],[]]))
        self.assertTrue(r['feasible'])
        self.assertEqual(r['metrics']['used_vans'],0)

    def test_roundtrip_and_exported_schemas(self):
        self.assertEqual(validate_scenario(json.loads(json.dumps(self.s))),self.s)
        for name,schema in SCHEMAS.items():
            exported=load_json(ROOT/f'schemas/{name}-v1.json')
            exported.pop('$schema')
            exported.pop('title')
            self.assertEqual(schema,exported)
        self.assertEqual(scenario_hash(self.s),scenario_hash(dict(reversed(list(self.s.items())))))

    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.json'
            path.write_text('{"x":1,"x":2}')
            with self.assertRaises(InputError):
                load_json(path)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.s=load_json(FIXTURE)

    def test_feasible_result_and_run_contract(self):
        r=solve(self.s,budget_s=5,restarts=2)
        check(r,RUN)
        self.assertEqual(r['feasibility'],'feasible')
        self.assertEqual(r['proof_status'],'not_certified')
        self.assertEqual(r['validation'],validate_solution(self.s,r['solution']))
        self.assertGreaterEqual(r['timing']['solve_s'],r['timing']['search_s'])
        self.assertLessEqual(r['timing']['time_to_first_feasible_s'],r['timing']['solve_s'])

    def test_supported_fleet_sizes(self):
        for count in range(1,6):
            with self.subTest(vans=count):
                self.s['vehicle_ids']=[f'van-{i}' for i in range(count)]
                result=solve(self.s,budget_s=5,restarts=1)
                self.assertEqual(result['feasibility'],'feasible')
                self.assertEqual(result['validation']['metrics']['available_vans'],count)

    def test_impossible_is_unknown_not_proven(self):
        r=solve(load_json(ROOT/'tests/fixtures/impossible.json'),budget_s=5)
        self.assertEqual(r['feasibility'],'unknown')
        self.assertEqual(r['termination_reason'],'search_exhausted')
        self.assertIsNone(r['solution'])

    def test_budget_and_evaluation_limits(self):
        r=solve(self.s,budget_s=0)
        self.assertEqual(r['termination_reason'],'budget_exhausted')
        self.assertEqual(r['feasibility'],'unknown')
        r=solve(self.s,budget_s=5,max_evaluations=0)
        self.assertEqual(r['termination_reason'],'evaluation_limit')
        self.assertEqual(r['evaluations'],0)
        for bad in [float('nan'), -1, True]:
            with self.assertRaises(InputError):
                solve(self.s,budget_s=bad)

    def test_deterministic_evaluation_limited_run(self):
        a=solve(self.s,seed=42,budget_s=30,max_evaluations=40,restarts=100)
        b=solve(self.s,seed=42,budget_s=30,max_evaluations=40,restarts=100)
        self.assertEqual(a['termination_reason'],'evaluation_limit')
        self.assertEqual(a['solution'],b['solution'])
        self.assertEqual(a['validation'],b['validation'])
        self.assertEqual(a['evaluations'],b['evaluations'])

    def test_zero_budget_with_fake_clock(self):
        class Clock:
            def __init__(self): self.t=0
            def __call__(self):
                self.t+=.1
                return self.t
        r=solve(self.s,budget_s=.25,clock=Clock())
        self.assertEqual(r['termination_reason'],'budget_exhausted')

    def test_neighborhoods_preserve_visits(self):
        moves=list(neighbors([['a','b'],['c']]))
        self.assertIn([['a'],['b','c']],moves)
        self.assertIn([['c','b'],['a']],moves)
        self.assertIn([['b','a'],['c']],moves)
        for routes in moves:
            self.assertEqual(sorted(sum(routes,[])),['a','b','c'])

    def test_tiny_exhaustive_reference(self):
        feasible=[]
        for order in itertools.permutations(['a','b']):
            for split in range(3):
                report=validate_solution(self.s,proposal(self.s,[list(order[:split]),list(order[split:])]))
                if report['feasible']:
                    feasible.append(objective_score(self.s,report))
        r=solve(self.s,budget_s=5)
        self.assertAlmostEqual(objective_score(self.s,r['validation']),min(feasible))
        # Equality to this tiny exhaustive test is not a solver optimality claim.

    def test_local_search_improves_construction(self):
        self.s.update(vehicle_ids=['van-1'],duration_cap_s=None)
        self.s['service']['constant_s']=0
        self.s['stops']=[dict(id=x,window_open_s=0,window_close_s=10000,
                              packages=0,mass_kg=0,volume_m3=0) for x in 'abcd']
        self.s['objective']['fuel_weight']=0
        times=iter([25,49,27,3,17,33,32,26,20,31,23,38,14,33,9,19,9,49,7,40])
        nodes=['depot',*'abcd']
        self.s['legs']=[dict(source=a,target=b,reachable=True,travel_s=next(times),
                             segments=[dict(distance_m=1000,driving_category='urban')])
                        for a in nodes for b in nodes if a!=b]
        initial=solve(self.s,budget_s=5,restarts=1,max_evaluations=11)
        improved=solve(self.s,budget_s=5,restarts=1)
        self.assertEqual(initial['validation']['metrics']['fleet_elapsed_s'],103)
        self.assertEqual(improved['validation']['metrics']['fleet_elapsed_s'],83)
        self.assertTrue(validate_solution(self.s,improved['solution'])['feasible'])

    def test_cross_vehicle_move_rechecks_capacity(self):
        self.s['capacity']['mass_kg']=20
        split=proposal(self.s,[['a'],['b']])
        self.assertTrue(validate_solution(self.s,split)['feasible'])
        for routes in neighbors([['a'],['b']]):
            if any(len(route)==2 for route in routes):
                self.assertFalse(validate_solution(self.s,proposal(self.s,routes))['feasible'])


class CLITests(unittest.TestCase):
    def cli(self,*args):
        return subprocess.run([sys.executable,'-m','engine',*map(str,args)],
                              cwd=ROOT,text=True,capture_output=True)

    def test_end_to_end_saved_run_and_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'result.json'
            run=self.cli('solve',FIXTURE,'--budget-s',5,'--output',path)
            self.assertEqual(run.returncode,0,run.stderr)
            saved=load_json(path)
            self.assertEqual(self.cli('validate',FIXTURE,'--solution',path).returncode,0)
            before=path.read_bytes()
            self.assertEqual(self.cli('solve',FIXTURE,'--output',path).returncode,1)
            self.assertEqual(path.read_bytes(),before)
            saved['validation']['metrics']['estimated_fuel_l']=0
            write_json_atomic(path,saved,overwrite=True)
            self.assertEqual(self.cli('validate',FIXTURE,'--solution',path).returncode,2)
            self.assertEqual(self.cli('solve',FIXTURE,'--output',path,'--overwrite').returncode,0)

    def test_cli_errors_and_no_solution(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'result.json'
            r=self.cli('solve',FIXTURE,'--budget-s',0,'--output',path)
            self.assertEqual(r.returncode,4,r.stderr)
            self.assertEqual(self.cli('validate',FIXTURE,'--solution',path).returncode,0)
            self.assertEqual(self.cli('solve',FIXTURE,'--budget-s','nan','--output',path).returncode,2)
            self.assertEqual(self.cli('validate',Path(directory)/'missing').returncode,1)
            path.write_text('{invalid')
            r=self.cli('validate',path)
            self.assertEqual(r.returncode,2)
            self.assertNotIn('Traceback',r.stderr)
            path.write_text(json.dumps(proposal(load_json(FIXTURE),[['a'],[]])))
            self.assertEqual(self.cli('validate',FIXTURE,'--solution',path).returncode,3)

    def test_atomic_serialization_failure_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'result.json'
            write_json_atomic(path,{'old':True})
            with self.assertRaises(ValueError):
                write_json_atomic(path,{'bad':float('nan')},overwrite=True)
            self.assertEqual(load_json(path),{'old':True})
            self.assertEqual(len(list(Path(directory).iterdir())),1)


if __name__=='__main__':
    unittest.main()
