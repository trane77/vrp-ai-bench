"""M4 invariants, independent labeling, portable models and transport integrity."""
import copy
import importlib.util
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.contracts import InputError, load_json
from engine.learn_data import label_state, layout_hash
from engine.learn_evaluate import case_schedule
from engine.learning import FEATURES, FEATURE_SCHEMA, Features, Policy, signed
from engine.moves import enumerate_moves
from engine.remote import bundle, unpack
from engine.solver import neighbor_moves, proposal, solve
from engine.storage import write_json_atomic
from engine.validation import objective_score, validate_solution


def fixture(): return load_json('tests/fixtures/two-stops.json')


def constant_model():
    return signed(dict(schema_version=1,kind='learned_policy',architecture=[30,32,16,2],feature_schema=FEATURE_SCHEMA,
        metadata={},layers=[dict(weight=[[0.]*a for _ in range(b)],bias=[0.]*b) for a,b in ((30,32),(32,16),(16,2))]),'model_hash')


class MovesTests(unittest.TestCase):
    def test_original_neighborhood_order_including_relocation_duplicates(self):
        routes=[['a','b'],[],['c']]; expected=[]
        for a,route in enumerate(routes):
            for i in range(len(route)):
                for b,dest in enumerate(routes):
                    for j in range(len(dest)+1):
                        if a==b and j in (i,i+1): continue
                        moved=copy.deepcopy(routes); stop=moved[a].pop(i)
                        moved[b].insert(j-1 if a==b and j>i else j,stop)
                        expected.append((moved,'relocate'))
        positions=[(a,i) for a,r in enumerate(routes) for i in range(len(r))]
        for (a,i),(b,j) in itertools.combinations(positions,2):
            moved=copy.deepcopy(routes); moved[a][i],moved[b][j]=moved[b][j],moved[a][i]
            expected.append((moved,'swap'))
        expected.append(([['b','a'],[],['c']],'reverse'))
        self.assertEqual(list(neighbor_moves(routes)),expected)
        self.assertEqual(routes,[['a','b'],[],['c']])

    def test_classic_evaluation_limit_regression(self):
        s=fixture()
        for method in ('ordered','shuffled'):
            result=solve(s,seed=10,budget_s=100,max_evaluations=12,restarts=1,selection_policy=method)
            self.assertEqual(result['evaluations'],12)
            self.assertTrue(result['validation']['feasible'])
            self.assertEqual(result['solution']['routes'][0]['stop_ids'],['a','b'])


class FeatureTests(unittest.TestCase):
    def test_complete_labels_match_independent_fixture_enumeration(self):
        s=fixture(); solution=proposal(s,[['a'],['b']]); group=label_state(s,solution)
        current=objective_score(s,validate_solution(s,solution))
        expected=[]
        for candidate,_ in neighbor_moves([['a'],['b']]):
            report=validate_solution(s,proposal(s,candidate))
            expected.append(max(0,current-objective_score(s,report)) if report['feasible'] else 0.)
        self.assertEqual([r['gain'] for r in group['rows']],expected)
        self.assertTrue(any(r['improvement'] for r in group['rows']))

    def test_features_never_call_candidate_validator_and_absence_is_zero(self):
        s=fixture(); routes=[['a','b'],[]]; report=validate_solution(s,proposal(s,routes))
        moves=list(enumerate_moves(routes)); move=next(m for m in moves if m.operator=='relocate' and m.b==1)
        with patch('engine.validation.validate_solution',side_effect=AssertionError('label leakage')):
            row=Features(s,routes,report).row(move)
        self.assertEqual(len(FEATURES),30); self.assertEqual(len(row),30)
        self.assertEqual(row[22:28],[0.]*6)

    def test_layout_ignores_ids_windows_and_order(self):
        s=dict(depot_id='d',locations=[dict(id='d',snapped=[0,0]),dict(id='a',snapped=[1,2]),dict(id='b',snapped=[3,4])])
        other=copy.deepcopy(s); other['locations'][1]['id']='new'; other['locations'].reverse()
        self.assertEqual(layout_hash(s),layout_hash(other))
        other['locations'][0]['snapped']=[5,6]
        self.assertNotEqual(layout_hash(s),layout_hash(other))

    def test_frozen_schedule_counts_and_controls(self):
        identifiers=[f'{c}-{s}' for c in ('bologna','modena') for s in range(3000,3004)]
        schedule=case_schedule(identifiers)
        self.assertEqual(len(schedule),336); self.assertEqual(sum(c['control'] for c in schedule),48)
        self.assertEqual(sum(c['budget_s'] for c in schedule),3120)
        self.assertEqual(schedule,case_schedule(identifiers))


class ModelTests(unittest.TestCase):
    def test_ties_are_stable_and_no_candidates_removed(self):
        s=fixture(); routes=[['a','b'],[]]; validation=validate_solution(s,proposal(s,routes)); moves=list(enumerate_moves(routes))
        self.assertEqual(Policy(constant_model()).order(s,routes,validation,moves),moves)

    def test_missing_incompatible_and_corrupt_models(self):
        with self.assertRaises(InputError): Policy.load(None)
        model=constant_model(); model['architecture']=[30,16,2]
        with self.assertRaises(InputError): Policy(model)
        model=signed({k:v for k,v in model.items() if k!='model_hash'},'model_hash')
        with self.assertRaises(InputError): Policy(model)

    def test_learned_cancellation_retains_validated_incumbent(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint=Path(tmp)/'checkpoint.json'; write_json_atomic(checkpoint,constant_model())
            from test_m2 import install_test_cities,FakeRoads
            from engine.city import CityRegistry
            from engine.drafts import make_draft,prepare_draft
            install_test_cities(Path(tmp)/'data'); manifest=CityRegistry(Path(tmp)/'data').get('bologna')
            road=prepare_draft(make_draft(manifest,count=2),manifest,FakeRoads())
            result=solve(road,budget_s=100,restarts=1,selection_policy='learned',checkpoint=checkpoint,cancelled=lambda:True)
            self.assertEqual(result['termination_reason'],'cancelled')
            s=fixture(); result=solve(s,budget_s=100,restarts=1,max_evaluations=30,selection_policy='learned',checkpoint=checkpoint)
            self.assertEqual(validate_solution(s,result['solution']),result['validation'])
            self.assertEqual(result['learned']['checkpoint_hash'],constant_model()['model_hash'])

    @unittest.skipUnless(importlib.util.find_spec('torch'),'requires frozen training container')
    def test_training_updates_weights_and_export_matches_cpu(self):
        from engine.learn_train import train
        s=fixture(); group=label_state(s,proposal(s,[['a'],['b']]))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); entries=[]
            for split in ('training','validation'):
                samples=signed(dict(schema_version=1,kind='learned_samples',split=split,scenario_hash=split,
                    feature_schema=FEATURE_SCHEMA,status='completed',groups=[group]),'samples_hash')
                path=root/f'{split}.json'; write_json_atomic(path,samples)
                entries.append(dict(path=path.name,samples_hash=samples['samples_hash']))
            collection=signed(dict(status='completed',dataset_hash='test',entries=entries,
                                   scenario_splits={'training':'training','validation':'validation'}),'collection_hash')
            write_json_atomic(root/'collection.json',collection)
            result=train(root/'collection.json',root/'training',try_gpu=False)
            self.assertEqual(result['completed_seeds'],[0,1,2])
            self.assertLess(result['parity_max_abs_error'],2e-6)
            self.assertNotEqual(Policy.load(result['checkpoint']).model['layers'],constant_model()['layers'])


class BundleTests(unittest.TestCase):
    def test_repeatable_import_and_corruption_rejected(self):
        import tarfile
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source'; source.mkdir(); (source/'value.json').write_text('{}')
            archive=root/'bundle.tar.gz'; bundle(source,archive)
            unpack(archive,root/'first'); unpack(archive,root/'second')
            self.assertEqual((root/'first/value.json').read_bytes(),(root/'second/value.json').read_bytes())
            (root/'first/unverified.json').write_text('unverified prior staging content')
            with self.assertRaisesRegex(InputError,'empty staging'): unpack(archive,root/'first')
            with tarfile.open(archive,'w:gz') as stream:
                (source/'value.json').write_text('changed'); stream.add(source/'value.json',arcname='value.json')
                inventory=root/'bundle.json'; inventory.write_text(json.dumps(dict(schema_version=1,files={'value.json':'0'*64})))
                stream.add(inventory,arcname='bundle.json')
            with self.assertRaisesRegex(InputError,'corrupt'): unpack(archive,root/'bad')
            self.assertFalse((root/'bad/value.json').exists())


class M4ContractTests(unittest.TestCase):
    def make_dataset(self,root):
        from test_m2 import install_test_cities,FakeRoads
        from engine.city import CityRegistry
        from engine.drafts import make_draft,prepare_draft
        from engine.learn_data import SPLITS
        from engine.solver import scenario_hash
        install_test_cities(root/'city-data'); registry=CityRegistry(root/'city-data'); entries=[]
        for city in ('bologna','modena'):
            manifest=registry.get(city)
            for split,seeds in SPLITS.items():
                for seed in seeds:
                    s=prepare_draft(make_draft(manifest,seed=seed),manifest,FakeRoads()); identifier=f'{city}-{seed}'; s['id']=identifier
                    path=f'scenarios/{identifier}.json'; write_json_atomic(root/'dataset'/path,s)
                    entries.append(dict(id=identifier,city=city,split=split,seed=seed,path=path,scenario_hash=scenario_hash(s),layout_hash=layout_hash(s)))
        value=signed(dict(schema_version=1,kind='learned_dataset',entries=entries,source_sha256='0'*64,constructed=True,scope='Synthetic fixtures only'),'dataset_hash')
        write_json_atomic(root/'dataset/dataset.json',value)
        return value

    def test_split_independence_checks_layout_even_with_new_ids_and_windows(self):
        from engine.learn_data import read_dataset
        from engine.solver import scenario_hash
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); value=self.make_dataset(root); read_dataset(root/'dataset/dataset.json')
            a=value['entries'][0]; b=next(e for e in value['entries'] if e['split']=='validation')
            source=load_json(root/'dataset'/a['path']); target=load_json(root/'dataset'/b['path'])
            target['locations']=source['locations']; target['legs']=source['legs']
            target['stops']=source['stops']; target['generation']['witness_routes']=source['generation']['witness_routes']
            target['stops'][0]['window_open_s']=0
            write_json_atomic(root/'dataset'/b['path'],target,overwrite=True)
            b.update(scenario_hash=scenario_hash(target),layout_hash=layout_hash(target))
            value=signed({k:v for k,v in value.items() if k!='dataset_hash'},'dataset_hash')
            write_json_atomic(root/'dataset/dataset.json',value,overwrite=True)
            with self.assertRaisesRegex(InputError,'duplicate layout'): read_dataset(root/'dataset/dataset.json')

    def test_v4_cancelled_case_references_replay_and_independent_audit(self):
        import multiprocessing
        import shutil
        from engine.experiments import execute_case, report
        from engine.learn_evaluate import freeze,archive_inputs,hydrate_run,audit,validate_manifest_v2
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=self.make_dataset(root)
            model=constant_model(); model['metadata']={'dataset_hash':data['dataset_hash']}
            model=signed({k:v for k,v in model.items() if k!='model_hash'},'model_hash')
            write_json_atomic(root/'checkpoint.json',model)
            manifest=freeze(root/'dataset/dataset.json',root/'checkpoint.json',root/'frozen')
            invalid=copy.deepcopy(manifest); invalid['cases'][0]['seed']=9
            invalid=signed({k:v for k,v in invalid.items() if k!='manifest_hash'},'manifest_hash')
            with self.assertRaises(InputError): validate_manifest_v2(invalid)
            directory=root/'campaign'; directory.mkdir(); write_json_atomic(directory/'manifest.json',manifest); archive_inputs(directory,manifest)
            for source in manifest['provenance']['source_hashes'] | manifest['provenance']['lock_hashes']:
                dest=directory/'sources'/source; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,dest)
            case=next(c for c in manifest['cases'] if c['method']=='learned'); cancel=multiprocessing.get_context('spawn').Event();cancel.set()
            process=multiprocessing.get_context('spawn').Process(target=execute_case,args=(str(directory/'cases'/case['id']),manifest,case,cancel))
            process.start();process.join(20);self.assertEqual(process.exitcode,0)
            run=load_json(directory/'cases'/case['id']/'result.json')
            self.assertEqual(run['schema_version'],4);self.assertNotIn('scenario',run)
            self.assertEqual(run['status'],'cancelled');self.assertEqual(run['checkpoint_hash'],model['model_hash'])
            self.assertIn('scenario',hydrate_run(directory,run))
            write_json_atomic(directory/'cases'/case['id']/'process.json',dict(started_monotonic_s=1,finished_monotonic_s=2,exitcode=0))
            value=audit(directory);self.assertFalse(value['campaign_complete']);self.assertEqual(len(value['unexecuted']),335)
            self.assertEqual(report(directory)['completed'],1)
            # A checkpoint changed after freezing must fail before solving.
            changed=copy.deepcopy(model);changed['layers'][0]['bias'][0]=1
            changed=signed({k:v for k,v in changed.items() if k!='model_hash'},'model_hash')
            write_json_atomic(root/'frozen/checkpoint.json',changed,overwrite=True)
            with self.assertRaisesRegex(InputError,'changed after freezing'): archive_inputs(root/'bad',manifest)


class DeadlineTests(unittest.TestCase):
    def test_expired_preparation_never_starts_remote_worker(self):
        from engine.remote import submit
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_json_atomic(root/'dataset.json',dict(dataset_hash='frozen'))
            write_json_atomic(root/'preparation.json',dict(dataset_hash='frozen',started_unix_s=0))
            with patch('engine.remote.ssh') as ssh:
                with self.assertRaisesRegex(InputError,'exceeded ten minutes'): submit('Ai360',root/'dataset.json')
                ssh.assert_not_called()

    def test_collection_deadline_labels_no_invented_states(self):
        import time
        from engine.learn_data import collect_trajectory
        with tempfile.TemporaryDirectory() as tmp:
            from test_m2 import install_test_cities,FakeRoads
            from engine.city import CityRegistry
            from engine.drafts import make_draft,prepare_draft
            root=Path(tmp);install_test_cities(root/'cities');manifest=CityRegistry(root/'cities').get('bologna')
            s=prepare_draft(make_draft(manifest,count=2),manifest,FakeRoads());path=root/'samples.json'
            result=collect_trajectory((s,dict(id=s['id'],split='training',scenario_hash='fixture'),
                                       'ordered',1,str(path),time.monotonic()-1,str(Path(tmp)/'cancel')))
            samples=load_json(path)
            self.assertEqual(samples['groups'],[])
            self.assertEqual(samples['feasibility'],'unknown')
            self.assertEqual(samples['termination_reason'],'cancelled')

    def test_label_cancellation_preserves_no_partial_neighborhood(self):
        s=fixture()
        with self.assertRaisesRegex(RuntimeError,'cancelled'): label_state(s,proposal(s,[['a'],['b']]),lambda:True)


class RoadResamplingTests(unittest.TestCase):
    def test_nearest_route_endpoint_mismatch_enters_recorded_resampling(self):
        from test_m2 import install_test_cities,FakeRoads
        from engine.city import CityRegistry
        from engine.drafts import make_draft,prepare_draft
        class InconsistentNearest(FakeRoads):
            bad=True
            def route(self,a,b,hash):
                result=super().route(a,b,hash)
                if self.bad and b['id']=='stop-001':
                    self.bad=False
                    result['geometry']=[a['snapped'],[b['snapped'][0]+.001,b['snapped'][1]]]
                return result
        with tempfile.TemporaryDirectory() as tmp:
            install_test_cities(tmp);manifest=CityRegistry(tmp).get('bologna');draft=make_draft(manifest,count=2)
            scenario=prepare_draft(draft,manifest,InconsistentNearest())
            self.assertEqual(scenario['generation']['rejected_samples'],1)
            points={p['id']:p['snapped'] for p in scenario['locations']}
            for leg in scenario['legs']:
                self.assertEqual(leg['geometry'][0],points[leg['source']])
                self.assertEqual(leg['geometry'][-1],points[leg['target']])
            self.assertTrue(validate_solution(scenario,proposal(scenario,scenario['generation']['witness_routes']))['feasible'])
