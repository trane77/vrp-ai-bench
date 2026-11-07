"""Frozen M4 held-out design and independent reconstruction audit."""
import copy
from pathlib import Path
import random
import shutil
import time

from .contracts import InputError, check, load_json, validate_scenario
from .contracts_v4 import MANIFEST_V2
from .experiments import provenance, report
from .learn_data import read_dataset
from .learning import Policy, signed, verify_hash
from .solver import proposal, scenario_hash
from .storage import write_json_atomic
from .validation import validate_solution


def case_schedule(instances):
    cases=[]
    for identifier in instances:
        for budget in (5,15):
            for seed in (0,1):
                for method in ('ordered','shuffled','learned'):
                    for weight in (1,.5,0):
                        cases.append(dict(instance=identifier,method=method,budget_s=budget,repetition=seed,seed=seed,
                                          time_weight=weight,recording=True,control=False))
    cases += [dict(c,recording=False,control=True) for c in cases if c['budget_s']==5 and c['time_weight']==1]
    random.Random(42).shuffle(cases)
    return [dict(c,id=f'case-{i:03d}') for i,c in enumerate(cases)]


def freeze(dataset_path, checkpoint, directory, deadline=None):
    data, scenarios=read_dataset(dataset_path); model=Policy.load(checkpoint).model
    if model['metadata']['dataset_hash'] != data['dataset_hash']: raise InputError('checkpoint belongs to another dataset')
    root=Path(directory).resolve(); root.mkdir(parents=True,exist_ok=True)
    instances=[]
    for entry in data['entries']:
        if entry['split'] != 'test': continue
        s=scenarios[entry['id']]; witness=proposal(s,s['generation']['witness_routes']); validation=validate_solution(s,witness)
        m=validation['metrics']
        instances.append(dict(id=entry['id'],scenario_hash=entry['scenario_hash'],scales=s['objective'],
            reference=dict(solution=witness,validation=validation,proof_status='not_certified'),
            hypervolume_reference=[max(1,2*m['fleet_elapsed_s']),max(1,2*m['estimated_fuel_l'])]))
        write_json_atomic(root/'scenarios'/f"{entry['id']}.json",s)
    write_json_atomic(root/'checkpoint.json',model)
    training={k:v for k,v in model['metadata'].items() if k not in ('history',)}
    value=signed(dict(schema_version=2,kind='experiment_manifest',design='m4-held-out-20-stops',planning_seed=42,
        main_cases=288,control_cases=48,instances=instances,cases=case_schedule([i['id'] for i in instances]),
        checkpoint_hash=model['model_hash'],dataset_hash=data['dataset_hash'],training=training,provenance=provenance(),
        artifact_root=str(root),deadline_monotonic_s=deadline,front_budget_multiplier=3,
        scope='Descriptive held-out results per constructed instance and repetition; no city generalization'), 'manifest_hash')
    validate_manifest_v2(value); write_json_atomic(root/'manifest.json',value)
    return value


def validate_manifest_v2(value):
    check(value,MANIFEST_V2); verify_hash(value,'manifest_hash')
    expected=[f'{city}-{seed}' for city in ('bologna','modena') for seed in range(3000,3004)]
    if [i['id'] for i in value['instances']] != expected or value['cases'] != case_schedule(expected) or value['planning_seed'] != 42 or value['design']!='m4-held-out-20-stops':
        raise InputError('invalid M4 frozen design')
    for i in value['instances']:
        metrics=i['reference']['validation']['metrics']
        if i['hypervolume_reference'] != [max(1,2*metrics['fleet_elapsed_s']),max(1,2*metrics['estimated_fuel_l'])]:
            raise InputError('invalid frozen hypervolume reference')
    return value


def archive_inputs(directory,manifest):
    source=Path(manifest['artifact_root'])
    model=Policy.load(source/'checkpoint.json').model
    if model['model_hash']!=manifest['checkpoint_hash']: raise InputError('checkpoint changed after freezing')
    write_json_atomic(directory/'checkpoint.json',model)
    for i in manifest['instances']:
        s=load_json(source/'scenarios'/f"{i['id']}.json")
        if scenario_hash(s)!=i['scenario_hash']: raise InputError('frozen scenario changed')
        if validate_solution(s,i['reference']['solution']) != i['reference']['validation']: raise InputError('reference validation mismatch')
        write_json_atomic(directory/'scenarios'/f"{i['id']}.json",s)


def hydrate_run(directory,run,scenario=None):
    if run.get('schema_version') != 4 or 'scenario_ref' not in run: return run
    path=(Path(directory)/run['scenario_ref']).resolve()
    if not path.is_relative_to(Path(directory).resolve()): raise InputError('unsafe scenario reference')
    original=load_json(path) if scenario is None else scenario
    s=dict(original,objective=dict(original['objective']))
    if scenario_hash(s)!=run['original_scenario_hash']: raise InputError('run scenario reference mismatch')
    s['objective'].update(time_weight=run['case']['time_weight'],fuel_weight=1-run['case']['time_weight'])
    if scenario_hash(s)!=run['scenario_hash']: raise InputError('weighted scenario mismatch')
    return dict(run,scenario=s)


def audit(directory, deadline=None):
    root=Path(directory); manifest=validate_manifest_v2(load_json(root/'manifest.json')); reconstructions=0; intervals=[]
    model=Policy.load(root/'checkpoint.json').model
    if model['model_hash']!=manifest['checkpoint_hash']: raise InputError('audit checkpoint mismatch')
    for source,digest in (manifest['provenance']['source_hashes'] | manifest['provenance']['lock_hashes']).items():
        import hashlib
        if hashlib.sha256((root/'sources'/source).read_bytes()).hexdigest()!=digest: raise InputError('archived source mismatch')
    scenarios={}
    for instance in manifest['instances']:
        s=validate_scenario(load_json(root/'scenarios'/f"{instance['id']}.json"))
        scenarios[instance['id']]=s
        if scenario_hash(s)!=instance['scenario_hash']: raise InputError('audit scenario mismatch')
        if validate_solution(s,instance['reference']['solution'])!=instance['reference']['validation']: raise InputError('audit witness mismatch')
        reconstructions+=1
    finished=[]
    for case in manifest['cases']:
        if deadline is not None and time.monotonic()>=deadline: raise RuntimeError('audit phase deadline')
        path=root/'cases'/case['id']
        if not (path/'result.json').exists(): continue
        run=hydrate_run(root,load_json(path/'result.json'),scenarios[case['instance']])
        if run['case']!=case or run.get('manifest_hash',manifest['manifest_hash'])!=manifest['manifest_hash']: raise InputError('audit case mismatch')
        if run.get('scenario'):
            if run['schema_version']!=4 or run['checkpoint_hash']!=(manifest['checkpoint_hash'] if case['method']=='learned' else None): raise InputError('audit run/checkpoint mismatch')
            candidates=run.get('observations',[])+run.get('alternatives',[])
            if run.get('solution'): candidates.append(dict(solution=run['solution'],validation=run['validation']))
            for item in candidates:
                report_=validate_solution(run['scenario'],item['solution'])
                if not report_['feasible'] or report_!=item['validation']: raise InputError('independent reconstruction mismatch')
                reconstructions+=1
        if (path/'events.jsonl').exists():
            import json
            events=[json.loads(line) for line in (path/'events.jsonl').read_text().splitlines()]
            observations=run.get('observations',[])
            if len(events)!=len(observations): raise InputError('replay event count mismatch')
            for index,(event,item) in enumerate(zip(events,observations)):
                if event['seq']!=index+1 or event['elapsed_s']!=item['elapsed_s'] or event['payload']['solution']!=item['solution'] or event['payload']['validation']!=item['validation']: raise InputError('replay mismatch')
        process=load_json(path/'process.json'); intervals.append((process['started_monotonic_s'],process['finished_monotonic_s']))
        if run['status']=='completed' and process['exitcode']==0: finished.append(case['id'])
    intervals.sort()
    if any(a[1]>b[0] for a,b in zip(intervals,intervals[1:])): raise InputError('overlapping final case processes')
    value=dict(schema_version=1,kind='learned_audit',reconstructions=reconstructions,finished_cases=len(finished),
               total=336,campaign_complete=len(finished)==336,status='passed',unexecuted=[c['id'] for c in manifest['cases'] if not (root/'cases'/c['id']/'result.json').exists()])
    write_json_atomic(root/'audit.json',value,overwrite=True)
    result=report(root); result['campaign_complete']=value['campaign_complete']
    write_json_atomic(root/'report.json',result,overwrite=True)
    return value
