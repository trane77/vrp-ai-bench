"""M2 unit/integration checks use explicitly simulated provider responses.
Real OSRM acceptance is separate: scripts/accept_m2.py.
"""
import copy
import gzip
import json
import multiprocessing
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from engine.city import CityRegistry, PRESETS, OSRM_IMAGE, manifest_hash
from engine.contracts import InputError, validate_scenario
from engine.contracts_v2 import RUN_V2, check
from engine.drafts import make_draft, prepare_draft, validate_draft
from engine.jobs import Jobs, Conflict, fold
from engine.routing import OSRM, ProviderError, Cancelled
from engine.solver import solve, scenario_hash
from engine.storage import write_json_atomic
from engine.validation import validate_solution
from engine.worker import Worker
try:
    from fastapi.testclient import TestClient
    from engine.service import create_app
    HAS_SERVICE=True
except ImportError:
    HAS_SERVICE=False


def install_test_cities(root):
    for id,preset in PRESETS.items():
        m=dict(schema_version=1,city_id=id,**preset,radius_m=5000,version='mocked-test-only',
            source_url='https://example.invalid/mock',source_sha256='0'*64,osm_timestamp='2026-01-01T00:00:00+00:00',
            osrm_image=OSRM_IMAGE,profile_sha256='1'*64,license='ODbL-1.0',attribution='MOCKED DATA — tests only',
            path_policy='osrm_car_minimum_profile_weight',files={})
        m['manifest_hash']=manifest_hash(m)
        write_json_atomic(Path(root)/'cities'/id/'manifest.json',m)


class FakeRoads:
    """Synthetic directed legs, never a production fallback."""
    delay=0
    def __init__(self,cancelled=lambda:False): self.cancelled=cancelled
    def snap(self,point):
        if self.cancelled(): raise Cancelled()
        return dict(requested=point,snapped=point,snap_distance_m=0)
    def route(self,a,b,hash):
        if self.cancelled(): raise Cancelled()
        if self.delay: time.sleep(self.delay)
        return dict(source=a['id'],target=b['id'],dataset_hash=hash,reachable=True,
                    travel_s=11,segments=[dict(distance_m=100,driving_category='urban')],
                    geometry=[a['snapped'],b['snapped']],raw_duration_s=10.1,
                    annotations=dict(distance_m=[100],duration_s=[10.1]))


def worker_process(root,data_root):
    FakeRoads.delay=.04
    w=Worker(root,data_root,FakeRoads).acquire()
    try: w.execute_one()
    finally: w.close()


class RoadContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)/'data'; install_test_cities(self.data)
        self.manifest=CityRegistry(self.data).get('bologna')
        self.draft=make_draft(self.manifest,count=3)
        self.s=prepare_draft(self.draft,self.manifest,FakeRoads())

    def test_v2_roundtrip_and_independent_accounting(self):
        self.assertEqual(validate_scenario(json.loads(json.dumps(self.s))),self.s)
        result=solve(self.s,budget_s=5,restarts=1)
        check(result,RUN_V2)
        self.assertEqual(result['schema_version'],2)
        self.assertTrue(result['validation']['feasible'])
        self.assertEqual(result['validation'],validate_solution(self.s,result['solution']))
        self.assertGreaterEqual(result['timing']['recording_s'],0)

    def test_mixed_cities_hashes_and_geometry_rejected(self):
        changes=[lambda s:s['dataset'].update(city_id='modena'),
                 lambda s:s['legs'][0].update(dataset_hash='wrong'),
                 lambda s:s['legs'][0].update(travel_s=10),
                 lambda s:s['locations'].pop(),
                 lambda s:s['locations'][0].update(snap_distance_m=101),
                 lambda s:s['legs'][0].update(geometry=[[0,0],[1,1]])]
        for change in changes:
            with self.subTest(change=change):
                s=copy.deepcopy(self.s);change(s)
                with self.assertRaises(InputError): validate_scenario(s)

    def test_manifest_tampering_and_city_boundaries(self):
        path=self.data/'cities/bologna/manifest.json'
        data=json.loads(path.read_text());data['version']='changed';path.write_text(json.dumps(data))
        with self.assertRaises(InputError):CityRegistry(self.data).get('bologna')
        self.draft['depot']=[0,0]
        with self.assertRaises(InputError):validate_draft(self.draft,self.manifest)

    def test_cancellation_preserves_independently_validated_incumbent(self):
        observed=[]
        def observe(kind,payload,elapsed):observed.append((kind,payload))
        def cancelled():return any(kind=='feasible_best' for kind,_ in observed)
        r=solve(self.s,budget_s=5,restarts=50,observe=observe,cancelled=cancelled)
        self.assertEqual(r['termination_reason'],'cancelled')
        self.assertEqual(r['job_status'],'cancelled')
        self.assertTrue(r['validation']['feasible'])
        self.assertTrue(any(kind=='accepted_insertion' and p['partial'] for kind,p in observed))

    def test_preparation_failure_does_not_replace_edited_inputs(self):
        class NoRoad(FakeRoads):
            def snap(self,point):raise InputError('no road')
        self.draft['mode']='edited_demo'
        with self.assertRaises(InputError):prepare_draft(self.draft,self.manifest,NoRoad())

    def test_unknown_versions_are_not_migrated(self):
        self.s['schema_version']=99
        with self.assertRaises(InputError):validate_scenario(self.s)


class ProviderTests(unittest.TestCase):
    def test_network_failure_is_not_no_route(self):
        with patch('urllib.request.urlopen',side_effect=TimeoutError()):
            with self.assertRaises(ProviderError):OSRM().snap([11,44])
        with self.assertRaises(InputError):OSRM('https://public.example')
        with self.assertRaises(Cancelled):OSRM(cancelled=lambda:True).snap([11,44])

    def test_nearest_and_bad_distance(self):
        provider=OSRM();provider.request=lambda _:dict(code='Ok',waypoints=[dict(location=[11,44],distance=101)])
        with self.assertRaises(InputError):provider.snap([11,44])
        provider.request=lambda _:dict(code='Ok',waypoints=[])
        with self.assertRaises(ProviderError):provider.snap([11,44])

    def test_route_annotations_and_explicit_unreachable(self):
        provider=OSRM();a=dict(id='a',snapped=[11,44]);b=dict(id='b',snapped=[12,44])
        provider.request=lambda _:dict(code='NoRoute')
        leg=provider.route(a,b,'hash');self.assertFalse(leg['reachable']);self.assertEqual(leg['geometry'],[])
        provider.request=lambda _:dict(code='Ok',routes=[dict(duration=10.1,geometry=dict(coordinates=[[11,44],[12,44]]),
            legs=[dict(annotation=dict(distance=[20,200],duration=[10,10]))])])
        leg=provider.route(a,b,'hash');self.assertEqual(leg['travel_s'],11)
        self.assertEqual([s['driving_category'] for s in leg['segments']],['slow','fast'])
        provider.request=lambda _:dict(code='Ok',routes=[])
        with self.assertRaises(ProviderError):provider.route(a,b,'hash')


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'runs';self.data=Path(self.temp.name)/'data'
        install_test_cities(self.data);self.jobs=Jobs(self.root)
        self.draft=self.jobs.save_draft(make_draft(CityRegistry(self.data).get('bologna'),count=3))

    def prepare_job(self):
        return self.jobs.submit('prepare',dict(draft_id=self.draft['id'],revision=self.draft['revision'],draft=self.draft['data']))

    def worker(self):
        worker=Worker(self.root,self.data,FakeRoads).acquire();self.addCleanup(worker.close);return worker

    def test_queue_and_worker_are_durable(self):
        job=self.prepare_job();reopened=Jobs(self.root)
        self.assertEqual(reopened.get(job['id'])['status'],'queued')
        self.worker().execute_one()
        self.assertEqual(reopened.get(job['id'])['status'],'completed')
        self.assertEqual(reopened.result(job['id'])['schema_version'],2)

    def test_exclusive_worker_and_interruption_recovery(self):
        job=self.prepare_job();worker=self.worker()
        with self.assertRaises(RuntimeError):Worker(self.root,self.data,FakeRoads).acquire()
        self.jobs.claim()
        self.jobs.emit(job['id'],'initial',dict(solution=None,partial=True),0)
        self.jobs.emit(job['id'],'feasible_best',dict(solution={'mock':True},partial=False),1)
        worker.close()
        next_worker=self.worker()
        self.assertEqual(self.jobs.get(job['id'])['termination_reason'],'interrupted')
        self.assertEqual(self.jobs.snapshot(job['id'])['best']['solution'],{'mock':True})
        self.assertTrue(list((self.root/job['id']).glob('*.gz')))

    def test_revision_conflict_and_queued_cancellation(self):
        old=self.draft['revision'];new=copy.deepcopy(self.draft['data']);new['seed']=43
        self.jobs.save_draft(new,self.draft['id'],old)
        with self.assertRaises(Conflict):self.prepare_job()
        self.draft=self.jobs.draft(self.draft['id']);job=self.prepare_job();cancelled=self.jobs.cancel(job['id'])
        self.assertEqual(cancelled['status'],'cancelled')
        self.assertFalse(self.worker().execute_one())
        self.assertEqual(self.jobs.get(job['id'])['status'],'cancelled')

    def test_solve_terminal_snapshot_equals_saved_result(self):
        prepare=self.prepare_job();worker=self.worker();worker.execute_one()
        s=self.jobs.result(prepare['id'])
        job=self.jobs.submit('solve',dict(draft_id=self.draft['id'],revision=self.draft['revision'],
            prepared_job_id=prepare['id'],scenario_hash=scenario_hash(s),seed=42,budget_s=5,restarts=2,max_evaluations=None))
        worker.execute_one();result=self.jobs.result(job['id'])
        recorded=self.jobs.events(job['id'])
        self.assertEqual([e['seq'] for e in recorded],list(range(1,len(recorded)+1)))
        self.assertEqual(self.jobs.events(job['id'],recorded[-1]['seq']),[])
        state=dict(seq=0,current=None,best=None,terminal=None)
        for event in recorded:state=fold(state,event)
        self.assertEqual(state['best']['solution'],result['solution'])
        self.assertEqual(state['best']['validation'],result['validation'])
        self.assertEqual(self.jobs.snapshot(job['id'])['best'],state['best'])
        self.assertEqual(self.jobs.snapshot(job['id'],1)['seq'],1)


@unittest.skipUnless(HAS_SERVICE,'install service/test dependencies from requirements.lock')
class ServiceTests(QueueTests):
    def client(self):
        return TestClient(create_app(self.root,self.data),base_url='http://localhost:8000')

    def test_api_revisions_and_prepared_identity(self):
        with self.client() as client:
            d=client.post('/api/v1/drafts',json=dict(city_id='bologna',count=3)).json()
            fleet_edit=copy.deepcopy(d['data']);fleet_edit['parameters']['vehicle_ids']=['van-1','van-2','van-3']
            d=client.put('/api/v1/drafts/'+d['id'],json=dict(revision=d['revision'],data=fleet_edit)).json()
            self.assertEqual(d['data']['mode'],'witness_demo')
            body=dict(kind='prepare',draft_id=d['id'],revision=d['revision'])
            r=client.post('/api/v1/jobs',json=body);self.assertEqual(r.status_code,202,r.text)
            id=r.json()['id'];worker=self.worker();worker.execute_one()
            self.assertEqual(client.get('/api/v1/jobs/'+id+'/editor').json()['mode'],'witness_demo')
            body.update(kind='solve',prepared_job_id=id)
            self.assertEqual(client.post('/api/v1/jobs',json=dict(body,policy='invalid')).status_code,422)
            from engine.learning import FEATURE_SCHEMA, signed
            from engine.storage import write_json_atomic
            write_json_atomic(self.root/'m4-imports'/'test'/'checkpoint.json',signed(dict(
                schema_version=1,kind='learned_policy',architecture=[30,32,16,2],feature_schema=FEATURE_SCHEMA,
                metadata={},layers=[dict(weight=[[0.]*a for _ in range(b)],bias=[0.]*b) for a,b in ((30,32),(32,16),(16,2))]),'model_hash'))
            learned=client.post('/api/v1/jobs',json=dict(body,policy='learned',restarts=1));self.assertEqual(learned.status_code,202)
            worker.execute_one()
            learned_run=client.get('/api/v1/jobs/'+learned.json()['id']+'/result').json()
            self.assertEqual(learned_run['learned']['inference'],'numpy-fp32')
            self.assertTrue(learned_run['validation']['feasible'])
            self.assertEqual(client.post('/api/v1/jobs',json=body).status_code,202)
            changed=copy.deepcopy(d['data']);changed['stops'][0]['mass_kg']=9999
            self.assertEqual(client.put('/api/v1/drafts/'+d['id'],json=dict(revision=d['revision'],data=changed)).status_code,200)
            self.assertEqual(client.post('/api/v1/jobs',json=body).status_code,409)
            self.assertEqual(client.get('/api/v1/jobs/missing').status_code,404)
            self.assertEqual(client.get('/api/v1/jobs/'+id+'/result').status_code,200)

    def test_origin_and_lan_host_rejected(self):
        with self.client() as client:
            self.assertEqual(client.post('/api/v1/drafts',json={},headers={'Origin':'http://evil.example'}).status_code,403)
            self.assertEqual(client.get('/api/v1/health',headers={'Host':'192.168.1.10'}).status_code,400)
            self.assertEqual(client.get('/api/v1/jobs/missing/events?after=-1').status_code,422)

    def test_reconnection_cursor_and_snapshot(self):
        job=self.prepare_job();self.worker().execute_one()
        with self.client() as client:
            with client.websocket_connect(f"ws://localhost:8000/api/v1/jobs/{job['id']}/stream?after=0",headers={'Origin':'http://localhost:8000'}) as ws:
                batch=ws.receive_json();self.assertEqual(batch['type'],'events')
                seq=batch['data'][-1]['seq'];self.assertEqual(ws.receive_json()['type'],'status')
            with client.websocket_connect(f"ws://localhost:8000/api/v1/jobs/{job['id']}/stream?after={seq}",headers={'Origin':'http://localhost:8000'}) as ws:
                self.assertEqual(ws.receive_json()['type'],'status')

    def test_http_disconnection_does_not_cancel_separate_worker(self):
        job=self.prepare_job()
        process=multiprocessing.get_context('spawn').Process(target=worker_process,args=(str(self.root),str(self.data)))
        process.start()
        with self.client() as client:self.assertEqual(client.get('/api/v1/jobs/'+job['id']).status_code,200)
        process.join(15)
        if process.is_alive():process.terminate();process.join();self.fail('worker failed to finish independently')
        self.assertEqual(process.exitcode,0)
        self.assertEqual(Jobs(self.root).get(job['id'])['status'],'completed')


if __name__=='__main__':unittest.main()
