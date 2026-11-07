"""M3 checks: complete tiny enumeration, frozen design and isolated queue execution."""
import copy
import itertools
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.adapters import SolverRequest, exact_integers, run_solver
from engine.contracts import InputError, load_json, validate_scenario
from engine.experiments import (compatible, freeze, hypervolume, nondominated, summarize_case,
                                validate_manifest, execute_case, report, campaign)
from engine.jobs import Jobs
from engine.solver import proposal, scenario_hash, solve
from engine.storage import write_json_atomic
from engine.validation import validate_solution
from test_m2 import install_test_cities, FakeRoads
from engine.city import CityRegistry
from engine.drafts import make_draft, prepare_draft

try:
    import ortools
    HAS_ORTOOLS=True
except ImportError:
    HAS_ORTOOLS=False


def enumerate_complete(s):
    """Every permutation and every distribution into labeled vehicle sequences."""
    ids=[v['id'] for v in s['stops']]
    feasible=[]
    for order in itertools.permutations(ids):
        for assignment in itertools.product(range(len(s['vehicle_ids'])),repeat=len(ids)):
            routes=[[stop for stop,vehicle in zip(order,assignment) if vehicle==v] for v in range(len(s['vehicle_ids']))]
            p=proposal(s,routes); r=validate_solution(s,p)
            if r['feasible']: feasible.append(r['metrics']['fleet_elapsed_s'])
    return min(feasible) if feasible else None


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.s=load_json('tests/fixtures/two-stops.json')
        self.s['objective'].update(time_weight=1,fuel_weight=0)

    @unittest.skipUnless(HAS_ORTOOLS,'optional OR-Tools not installed')
    def test_all_adapters_against_complete_enumeration(self):
        variants=[self.s]
        decimal=copy.deepcopy(self.s); decimal['capacity'].update(mass_kg=.3,volume_m3=.3)
        for stop,mass in zip(decimal['stops'],(.1,.2)):stop['mass_kg']=mass
        variants.append(decimal)
        blocked=copy.deepcopy(self.s);blocked['legs'][1].update(reachable=False,travel_s=0,segments=[])
        variants.append(blocked)
        capped=copy.deepcopy(self.s);capped['duration_cap_s']=89;variants.append(capped)
        late=copy.deepcopy(self.s);late['stops'][1]['window_close_s']=49;late['stops'][1]['window_open_s']=49;variants.append(late)
        nonzero=copy.deepcopy(self.s);nonzero['start_s']=5;variants.append(nonzero)
        for s in variants:
            s=validate_scenario(s); optimum=enumerate_complete(s)
            for method in ('ordered','shuffled','ortools'):
                with self.subTest(method=method,capacity=s['capacity'],optimum=optimum):
                    result=run_solver(SolverRequest(s,method,1.0,0))
                    self.assertEqual(result['proof_status'],'not_certified')
                    if optimum is None:
                        self.assertIsNone(result['solution']);self.assertEqual(result['feasibility'],'unknown')
                    else:
                        self.assertTrue(result['validation']['feasible'])
                        self.assertEqual(result['validation'],validate_solution(s,result['solution']))
                        self.assertEqual(result['validation']['metrics']['fleet_elapsed_s'],optimum)
        self.assertEqual(enumerate_complete(self.s),90)  # waiting + service past close + loaded return

    def test_exact_decimal_scaling_and_rejection(self):
        scale,values=exact_integers([.3,.1,.2]);self.assertEqual(values[0],sum(values[1:]));self.assertEqual(scale,10)
        for values in ([1e-19],[2**60],[1e100]):
            with self.assertRaises(InputError):exact_integers(values)

    def test_policy_preserves_m2_default_and_construction_rng(self):
        a=solve(self.s,seed=2,budget_s=5,max_evaluations=20)
        b=run_solver(SolverRequest(self.s,'ordered',5,2))
        self.assertEqual(a['solution'],b['solution'])
        with self.assertRaises(InputError):solve(self.s,selection_policy='invalid')
        result=run_solver(SolverRequest(self.s,'shuffled',0,1))
        self.assertIsNone(result['solution']);self.assertEqual(result['termination_reason'],'budget_exhausted')

    @unittest.skipUnless(HAS_ORTOOLS,'optional OR-Tools not installed')
    def test_ortools_callbacks_validated_and_unsupported_objective(self):
        observations=[]
        r=run_solver(SolverRequest(self.s,'ortools',.15,None,lambda k,p,t:observations.append(p)))
        for p in observations:self.assertEqual(p['validation'],validate_solution(self.s,p['solution']))
        self.assertFalse(r['native']['random_seed_control'])
        s=copy.deepcopy(self.s);s['objective']['fuel_weight']=.5
        with self.assertRaises(InputError):run_solver(SolverRequest(s,'ortools',.1))

    def test_late_validation_and_target_censoring(self):
        p=proposal(self.s,[['a','b'],[]]);r=validate_solution(self.s,p)
        reference=dict(validation=r)
        case=dict(budget_s=5)
        late=dict(elapsed_s=5.001,validation=r)
        quality=summarize_case(self.s,case,[late],reference)
        self.assertFalse(quality['feasible_within_budget']);self.assertTrue(quality['target_censored'])
        self.assertEqual(quality['target_time_s'],5)
        quality=summarize_case(self.s,case,[dict(late,elapsed_s=5)],reference)
        self.assertTrue(quality['feasible_within_budget']);self.assertFalse(quality['target_censored'])

    def test_front_and_frozen_hypervolume(self):
        def item(x,y):return dict(validation=dict(metrics=dict(fleet_elapsed_s=x,estimated_fuel_l=y)))
        values=[item(2,4),item(3,3),item(4,4),item(2,4)]
        self.assertEqual(len(nondominated(values)),2)
        self.assertEqual(hypervolume(values,[6,6],dict(time_scale_s=1,fuel_scale_l=1)),11)
        self.assertEqual(hypervolume([item(7,1)],[6,6],dict(time_scale_s=1,fuel_scale_l=1)),0)


class CampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();root=Path(cls.temp.name)
        install_test_cities(root/'data');registry=CityRegistry(root/'data')
        scenarios=[]
        for city in ('bologna','modena'):
            manifest=registry.get(city)
            scenarios.append(prepare_draft(make_draft(manifest,count=20),manifest,FakeRoads()))
        cls.manifest=freeze(scenarios)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_design_hashes_and_references(self):
        m=copy.deepcopy(self.manifest);validate_manifest(m)
        self.assertEqual(len(m['cases']),102)
        self.assertEqual(sum(not c['control'] for c in m['cases']),84)
        self.assertEqual(sum(c['control'] for c in m['cases']),18)
        for i in m['instances']:
            self.assertEqual(i['scenario_hash'],scenario_hash(i['scenario']))
            self.assertTrue(validate_solution(i['scenario'],i['reference']['solution'])['feasible'])
        m['cases'][0]['budget_s']=1
        with self.assertRaises(InputError):validate_manifest(m)
        m=copy.deepcopy(self.manifest);m['instances'][0]['hypervolume_reference'][0]+=1
        m['manifest_hash']=scenario_hash({k:v for k,v in m.items() if k!='manifest_hash'})
        with self.assertRaises(InputError):validate_manifest(m)

    def test_compatibility(self):
        a=self.manifest['cases'][0]
        self.assertTrue(compatible(a,dict(a,method='ordered')))
        for change in (dict(instance='other'),dict(budget_s=999),dict(time_weight=.3),dict(recording=not a['recording'])):
            self.assertFalse(compatible(a,dict(a,**change)))

    def test_cancelled_child_isolated_and_artifact_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_json_atomic(root/'manifest.json',self.manifest)
            for instance in self.manifest['instances']:
                write_json_atomic(root/'scenarios'/f"{instance['id']}.json",instance['scenario'])
            case=dict(self.manifest['cases'][0],method='ordered',seed=0)
            cancel=multiprocessing.get_context('spawn').Event();cancel.set()
            p=multiprocessing.get_context('spawn').Process(target=execute_case,args=(str(root/'cases'/case['id']),self.manifest,case,cancel))
            p.start();p.join(20)
            self.assertFalse(p.is_alive());self.assertEqual(p.exitcode,0)
            result=load_json(root/'cases'/case['id']/'result.json')
            self.assertEqual(result['status'],'cancelled')
            self.assertTrue(result['run_id'].endswith('/'+case['id']))
            summary=load_json(root/'cases'/case['id']/'summary.json')
            self.assertEqual(summary['schema_version'],1);self.assertNotIn('scenario',summary)
            output=report(root);self.assertEqual(output['completed'],1)
            self.assertTrue((root/'report.csv').exists());self.assertTrue((root/'report.md').exists())

    def test_queue_cancellation_and_recovery_preserve_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs=Jobs(tmp);job=jobs.submit('experiment',dict(manifest=self.manifest));jobs.claim()
            directory=jobs.root/job['id'];directory.mkdir();write_json_atomic(directory/'manifest.json',self.manifest)
            write_json_atomic(directory/'cases'/'case-000'/'result.json',dict(case=self.manifest['cases'][0],status='failed',termination_reason='adapter_error'))
            jobs.recover()
            self.assertEqual(jobs.get(job['id'])['termination_reason'],'interrupted')
            self.assertTrue((directory/'cases'/'case-000'/'result.json').exists())
            next_job=jobs.submit('experiment',dict(manifest=self.manifest));jobs.cancel(next_job['id'])
            output=campaign(jobs,next_job['id'],self.manifest)
            self.assertEqual(output['completed'],0)

    def test_worker_process_sequence_cancellation_and_no_m2_overlap(self):
        from engine.worker import Worker
        import os
        with tempfile.TemporaryDirectory() as tmp:
            jobs=Jobs(tmp);job=jobs.submit('experiment',dict(manifest=self.manifest))
            draft=jobs.save_draft(make_draft(CityRegistry(Path(self.temp.name)/'data').get('bologna'),count=3))
            later=jobs.submit('prepare',dict(draft_id=draft['id'],revision=draft['revision'],draft=draft['data']))
            worker=Worker(tmp).acquire();self.addCleanup(worker.close)
            original=worker.jobs.emit
            def emit(id,kind,payload,elapsed):
                event=original(id,kind,payload,elapsed)
                if kind=='metric':worker.jobs.cancel(id)
                return event
            worker.jobs.emit=emit
            self.assertTrue(worker.execute_one())
            output=jobs.result(job['id']);self.assertEqual(output['completed'],1)
            self.assertEqual(jobs.get(job['id'])['status'],'cancelled')
            self.assertEqual(jobs.get(later['id'])['status'],'queued')
            paths=list((jobs.root/job['id']/'cases').glob('*/process.json'));self.assertEqual(len(paths),1)
            self.assertNotEqual(load_json(paths[0])['pid'],os.getpid())
            from engine.contracts_v2 import EVENT
            from engine.contracts import check
            for path in (jobs.root/job['id']/'cases').glob('*/events.jsonl'):
                for line in path.read_text().splitlines():check(json.loads(line),EVENT)

    def test_http_experiment_contract_and_exports(self):
        from fastapi.testclient import TestClient
        from engine.service import create_app
        with tempfile.TemporaryDirectory() as tmp, TestClient(create_app(tmp,client_dir='/missing'),base_url='http://localhost') as client:
            response=client.post('/api/v1/experiments',json=dict(manifest=self.manifest));self.assertEqual(response.status_code,202)
            id=response.json()['id']
            self.assertNotIn('instances',response.json()['request']['manifest'])
            listing=client.get('/api/v1/experiments').json()
            self.assertNotIn('instances',listing[0]['request']['manifest'])
            self.assertEqual(client.get(f'/api/v1/experiments/{id}/manifest').json(),self.manifest)
            case=self.manifest['cases'][0]
            scenario=next(i['scenario'] for i in self.manifest['instances'] if i['id']==case['instance'])
            write_json_atomic(Path(tmp)/id/'cases'/case['id']/'result.json',dict(case=case,status='completed',termination_reason='search_exhausted',scenario=scenario,observations=[],objective=scenario['objective'],provenance=dict(marker='excluded from summary')))
            summary=client.get(f"/api/v1/experiments/{id}/cases/{case['id']}?view=summary").json()
            self.assertTrue(summary['scenario_available']);self.assertNotIn('scenario',summary);self.assertNotIn('provenance',summary)
            self.assertIn('scenario',client.get(f"/api/v1/experiments/{id}/cases/{case['id']}").json())
            self.assertEqual(len(client.get(f'/api/v1/experiments/{id}/cases').json()),102)
            self.assertEqual(client.get(f'/api/v1/experiments/{id}/cases/../../secret').status_code,404)
            self.assertEqual(client.get(f'/api/v1/experiments/{id}/cases/unknown/events').status_code,404)
            self.assertEqual(client.get(f'/api/v1/experiments/{id}/report').status_code,409)
            self.assertEqual(client.get(f'/api/v1/experiments/{id}/export/html').status_code,422)
            self.assertTrue(client.post(f'/api/v1/experiments/{id}/cancel').json()['cancel_requested'])
            manifest=copy.deepcopy(self.manifest);manifest['manifest_hash']='bad'
            self.assertEqual(client.post('/api/v1/experiments',json=dict(manifest=manifest)).status_code,422)

    def test_report_pairs_ortools_temporal_repetitions_without_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_json_atomic(root/'manifest.json',self.manifest)
            selected=[c for c in self.manifest['cases'] if c['instance']=='bologna' and c['budget_s']==5 and c['time_weight']==1 and c['repetition']==0 and c['recording']]
            self.assertEqual(len(selected),3)
            for index,case in enumerate(selected):
                write_json_atomic(root/'cases'/case['id']/'result.json',dict(case=case,status='completed',termination_reason='search_exhausted',quality=dict(best_score=index+1)))
            output=report(root)
            self.assertEqual(len(output['paired_differences']),3)
            self.assertEqual(sum(p['pairing']=='temporal_repetition' for p in output['paired_differences']),2)
            self.assertEqual(sum(p['pairing']=='custom_seed' for p in output['paired_differences']),1)
            self.assertTrue(all(p['b_minus_a'] is not None for p in output['paired_differences']))

    def test_modified_frozen_scenario_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=dict(self.manifest['cases'][0],method='ordered',seed=0)
            instance=next(i for i in self.manifest['instances'] if i['id']==case['instance'])
            changed=copy.deepcopy(instance['scenario']);changed['id']='tampered'
            write_json_atomic(root/'scenarios'/f"{case['instance']}.json",changed)
            cancel=multiprocessing.get_context('spawn').Event()
            execute_case(str(root/'cases'/case['id']),self.manifest,case,cancel)
            result=load_json(root/'cases'/case['id']/'result.json')
            self.assertEqual(result['status'],'failed');self.assertIn('hash mismatch',result['error'])
