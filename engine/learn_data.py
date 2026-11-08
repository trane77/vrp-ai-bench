"""Frozen road-backed splits and complete-neighborhood supervised observations."""
import concurrent.futures
import copy
import multiprocessing
from pathlib import Path
import time

from .contracts import InputError, load_json, validate_scenario
from .drafts import make_draft, prepare_draft
from .learning import Features, FEATURE_SCHEMA, FEATURE_SCHEMA_V2, FEATURE_SCHEMA_V3, signed, verify_hash
from .moves import enumerate_moves
from .solver import proposal, scenario_hash, solve
from .storage import write_json_atomic
from .validation import objective_score, validate_solution

SPLITS = {'training': range(1000,1012), 'validation': range(2000,2004), 'test': range(3000,3004)}


def layout_hash(s):
    # Ignore IDs, appointment times, demands and list ordering.
    return scenario_hash(sorted(p['snapped'] for p in s['locations'] if p['id'] != s['depot_id']))


def _prepare_one(task):
    from .routing import OSRM
    manifest,seed,identifier,path,deadline = task
    if Path(path).exists(): return
    stopped=lambda: deadline is not None and time.monotonic()>=deadline
    s=prepare_draft(make_draft(manifest,seed=seed),manifest,OSRM(cancelled=stopped),cancelled=stopped)
    s['id']=identifier
    write_json_atomic(path,s)


def dataset(directory, data_root='data', deadline=None, cancelled=lambda:False):
    from .city import CityRegistry, verify_service
    from .routing import OSRM
    preparation_started=time.time()
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    registry = CityRegistry(data_root)
    entries = []; layouts = set(); source = None
    def stopped(): return cancelled() or (deadline is not None and time.monotonic() >= deadline)
    manifests={city:registry.get(city) for city in ('bologna','modena')}
    for manifest in manifests.values(): verify_service(registry,manifest)
    tasks=[(manifests[city],seed,f'{city}-{seed}',str(root/'scenarios'/f'{city}-{seed}.json'),deadline)
           for city in manifests for seeds in SPLITS.values() for seed in seeds]
    with concurrent.futures.ProcessPoolExecutor(max_workers=4,mp_context=multiprocessing.get_context('spawn')) as pool:
        for _ in pool.map(_prepare_one,tasks):
            if stopped(): raise RuntimeError('dataset preparation deadline or cancellation')
    for city in ('bologna','modena'):
        manifest = manifests[city]
        if source is not None and source != manifest['source_sha256']: raise InputError('mixed road snapshots')
        source = manifest['source_sha256']
        for split, seeds in SPLITS.items():
            for seed in seeds:
                if stopped(): raise RuntimeError('dataset preparation deadline or cancellation')
                identifier = f'{city}-{seed}'
                path = root/'scenarios'/f'{identifier}.json'
                if path.exists():
                    s = validate_scenario(load_json(path))
                else:
                    s = prepare_draft(make_draft(manifest, seed=seed), manifest, OSRM(cancelled=stopped), cancelled=stopped)
                    s['id'] = identifier
                    write_json_atomic(path, s)
                if s['generation']['seed'] != seed or s['dataset']['manifest_hash'] != manifest['manifest_hash']:
                    raise InputError('existing scenario has incompatible generation/provenance')
                fingerprint = layout_hash(s)
                if fingerprint in layouts: raise InputError('duplicate stop layout across dataset')
                layouts.add(fingerprint)
                report = validate_solution(s, proposal(s, s['generation']['witness_routes']))
                if not report['feasible']: raise InputError('invalid dataset witness')
                entries.append(dict(id=identifier, city=city, seed=seed, split=split, path=f'scenarios/{identifier}.json',
                                    scenario_hash=scenario_hash(s), layout_hash=fingerprint))
    value = signed(dict(schema_version=1, kind='learned_dataset', entries=entries, source_sha256=source,
                        constructed=True, scope='Constructed instances, no city generalization'), 'dataset_hash')
    write_json_atomic(root/'dataset.json', value)
    write_json_atomic(root/'preparation.json',dict(schema_version=1,started_unix_s=preparation_started,
        finished_unix_s=time.time(),duration_s=time.time()-preparation_started,dataset_hash=value['dataset_hash']))
    return value


def read_dataset(path):
    path = Path(path); value = load_json(path); verify_hash(value, 'dataset_hash')
    if value.get('schema_version') != 1 or value.get('kind') != 'learned_dataset': raise InputError('incompatible dataset')
    expected = {(city,split,seed) for city in ('bologna','modena') for split,seeds in SPLITS.items() for seed in seeds}
    if len(value['entries']) != 40 or {(e['city'],e['split'],e['seed']) for e in value['entries']} != expected:
        raise InputError('dataset does not match frozen 40-instance design')
    seen = set(); scenarios = {}
    for e in value['entries']:
        if e['id'] != f"{e['city']}-{e['seed']}" or e['path'] != f"scenarios/{e['id']}.json":
            raise InputError('scenario IDs/paths must match frozen city and seed')
        file = (path.parent/e['path']).resolve()
        if not file.is_relative_to(path.parent.resolve()): raise InputError('unsafe scenario path')
        s = validate_scenario(load_json(file))
        if s['id'] != e['id'] or scenario_hash(s) != e['scenario_hash'] or layout_hash(s) != e['layout_hash']:
            raise InputError('dataset scenario/hash mismatch')
        if e['layout_hash'] in seen: raise InputError('duplicate layout between scenarios/splits')
        seen.add(e['layout_hash'])
        if s['dataset']['city_id'] != e['city'] or s['generation']['seed'] != e['seed'] or s['dataset']['source_sha256'] != value['source_sha256']:
            raise InputError('dataset provenance mismatch')
        if len(s['stops']) != 20 or len(s['vehicle_ids']) != 2 or s['duration_cap_s'] is not None or s['service']['mode'] != 'constant' or s['service']['constant_s'] != 60:
            raise InputError('dataset scenario parameters differ from M4')
        if not validate_solution(s, proposal(s, s['generation']['witness_routes']))['feasible']:
            raise InputError('invalid witness')
        scenarios[e['id']] = s
    return value, scenarios


def label_state(s, solution, cancelled=lambda:False, version=1):
    report = validate_solution(s, solution)
    if not report['feasible']: raise InputError('cannot label an invalid current state')
    routes = [r['stop_ids'] for r in solution['routes']]
    features = Features(s, routes, report, version=version); current = objective_score(s, report)
    rows = []
    for index, move in enumerate(enumerate_moves(routes)):
        if cancelled(): raise RuntimeError('labeling cancelled or phase deadline')
        x = features.row(move)  # Deliberately computed before independent candidate validation.
        result = validate_solution(s, proposal(s, move.apply(routes)))
        gain = max(0., current-objective_score(s, result)) if result['feasible'] else 0.
        rows.append(dict(index=index, features=x, improvement=gain > 0, gain=gain))
    return dict(solution=solution, validation=report, rows=rows)


def collect_trajectory(task):
    if len(task) == 8:
        s, entry, method, weight, output, deadline, cancel_path, version = task
    else:
        s, entry, method, weight, output, deadline, cancel_path = task
        version = 1
    stopped=lambda: time.monotonic()>=deadline or Path(cancel_path).exists()
    started = time.monotonic(); states = []
    s = copy.deepcopy(s); s['objective'].update(time_weight=weight, fuel_weight=1-weight)
    schema_map = {1: FEATURE_SCHEMA, 2: FEATURE_SCHEMA_V2, 3: FEATURE_SCHEMA_V3}
    schema = schema_map.get(version, FEATURE_SCHEMA)
    def observe(kind,payload,elapsed):
        if kind == 'feasible_best' and len(states) < 3: states.append(copy.deepcopy(payload['solution']))
    value = dict(schema_version=1, kind='learned_samples', scenario_id=entry['id'], split=entry['split'],
                 scenario_hash=entry['scenario_hash'], method=method, time_weight=weight, seed=10,
                 feature_schema=schema, groups=[])
    try:
        result = solve(s, seed=10, restarts=1, max_evaluations=1000, budget_s=min(30., max(0,deadline-time.monotonic())), observe=observe,
                       selection_policy=method, cancelled=stopped)
        value.update(termination_reason=result['termination_reason'], feasibility=result['feasibility'])
        for state in states:
            if stopped(): raise RuntimeError('collection phase deadline or cancellation')
            value['groups'].append(label_state(s, state, stopped, version=version))
        value['status'] = 'completed'
    except Exception as exc:
        value.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    value['duration_s'] = time.monotonic()-started
    value = signed(value, 'samples_hash'); write_json_atomic(output, value)
    return dict(path=Path(output).name, samples_hash=value['samples_hash'], status=value['status'], groups=len(value['groups']))


def collect(dataset_path, directory, deadline=None, cancelled=lambda:False, processes=4, version=1, timeout_s=None):
    data, scenarios = read_dataset(dataset_path); root = Path(directory); root.mkdir(parents=True,exist_ok=True)
    if timeout_s is not None:
        deadline = time.monotonic() + timeout_s
    else:
        deadline = deadline if deadline is not None else time.monotonic() + 25 * 60
    tasks = [(scenarios[e['id']],e,method,weight,str(root/f"{e['id']}-{method}-{weight}.json"),deadline,str(root/'collection.cancel'),version)
             for e in data['entries'] if e['split'] != 'test' for method in ('ordered','shuffled') for weight in (1,.5,0)]
    entries = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=processes, mp_context=multiprocessing.get_context('spawn')) as pool:
        # Bounded submission: cancellation/deadline never launches new trajectories.
        pending = {}; cursor = 0
        while pending or cursor < len(tasks):
            if cancelled(): (root/'collection.cancel').touch()
            while len(pending) < processes and cursor < len(tasks) and not cancelled() and time.monotonic()<deadline:
                pending[pool.submit(collect_trajectory,tasks[cursor])] = cursor; cursor += 1
            if not pending: break
            done,_ = concurrent.futures.wait(pending,timeout=.2,return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                entries.append(future.result()); del pending[future]
    value = signed(dict(schema_version=1,kind='sample_collection',dataset_hash=data['dataset_hash'],
                        entries=sorted(entries,key=lambda e:e['path']), expected=len(tasks),
                        scenario_splits={e['scenario_hash']:e['split'] for e in data['entries']},
                        status='completed' if len(entries)==len(tasks) and all(e['status']=='completed' for e in entries) else 'incomplete'), 'collection_hash')
    write_json_atomic(root/'collection.json',value)
    return value
