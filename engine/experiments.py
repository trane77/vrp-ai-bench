"""Frozen pilot design, isolated sequential execution and descriptive reporting."""
import copy
import csv
import hashlib
import importlib.metadata
import io
import json
import multiprocessing
import os
from pathlib import Path
import random
import resource
import statistics
import subprocess
import time

from .adapters import SolverRequest, run_solver
from .contracts import InputError, check, load_json, validate_scenario
from .contracts_v3 import MANIFEST_V1, RUN_V3, REPORT_V1
from .solver import proposal, runtime_info, scenario_hash
from .storage import write_json_atomic
from .validation import objective_score, validate_solution

METHODS = ('ordered', 'shuffled', 'ortools')


def provenance():
    def git(*args):
        return subprocess.check_output(['git', *args], text=True).strip()
    portable = load_json('source-provenance.json') if Path('source-provenance.json').exists() else None
    return dict(runtime=runtime_info(), container_image=os.environ.get('VRP_CONTAINER_IMAGE'),
                git_revision=portable['git_revision'] if portable else git('rev-parse', 'HEAD'),
                git_dirty=portable['git_dirty'] if portable else git('status', '--porcelain'),
                lock_hashes={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                             map(Path, ['requirements.lock', 'requirements-experiment.lock', 'client/package-lock.json']) if p.exists()},
                packages={d.metadata['Name']: d.version for d in importlib.metadata.distributions()},
                source_hashes={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path('engine').glob('*.py'))},
                native_threads={k: os.environ.get(k) for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS')})


def freeze(scenarios):
    instances = []
    for data in scenarios:
        s = validate_scenario(data)
        if s['schema_version'] != 2 or len(s['stops']) != 20:
            raise InputError('pilot requires two M2 scenarios with 20 stops each')
        witness = proposal(s, s['generation']['witness_routes'])
        report = validate_solution(s, witness)
        if not report['feasible']: raise InputError('pilot witness is not feasible')
        m = report['metrics']
        instances.append(dict(id=s['dataset']['city_id'], scenario=s, scenario_hash=scenario_hash(s),
                              reference=dict(solution=witness, validation=report, proof_status='not_certified'),
                              scales=copy.deepcopy(s['objective']),
                              hypervolume_reference=[max(1, 2*m['fleet_elapsed_s']), max(1, 2*m['estimated_fuel_l'])]))
    if sorted(i['id'] for i in instances) != ['bologna', 'modena']:
        raise InputError('pilot requires one Bologna and one Modena scenario')
    cases = []
    for instance in instances:
        for budget in (5, 15):
            for repetition in range(3):
                for method in METHODS:
                    for weight in ((1,) if method == 'ortools' else (1, .5, 0)):
                        cases.append(dict(instance=instance['id'], method=method, budget_s=budget,
                                          repetition=repetition, seed=None if method == 'ortools' else repetition,
                                          time_weight=weight, recording=True, control=False))
    controls = [dict(c, recording=False, control=True) for c in cases if c['budget_s']==5 and c['time_weight']==1]
    cases += controls
    random.Random(42).shuffle(cases)
    for index, case in enumerate(cases): case['id'] = f'case-{index:03d}'
    manifest = dict(schema_version=1, kind='experiment_manifest', design='m3-two-constructed-instances',
                    planning_seed=42, main_cases=84, control_cases=18, instances=instances, cases=cases,
                    provenance=provenance(), front_budget_multiplier=3,
                    scope='Descriptive pilot on two constructed instances; no city generalization')
    manifest['manifest_hash'] = scenario_hash(manifest)
    return manifest


def freeze_scale_campaign(scenarios, *, methods=('ordered', 'shuffled', 'learned'), budgets=(5, 15), weights=(1, 0.5, 0), repetitions=1, checkpoint_model=None):
    if not scenarios:
        raise InputError('at least one scenario is required')
    valid_methods = ('ordered', 'shuffled', 'learned')
    if not methods or any(m not in valid_methods for m in methods):
        raise InputError('methods must be a subset of ordered, shuffled, learned')
    if 'learned' in methods and checkpoint_model is None:
        raise InputError('learned method requires a verified checkpoint model')
    if not budgets or any(b not in (5, 15) for b in budgets):
        raise InputError('budgets must be chosen from (5, 15)')
    if not weights or any(w not in (1, 0.5, 0) for w in weights):
        raise InputError('weights must be chosen from (1, 0.5, 0)')
    if type(repetitions) is not int or not (1 <= repetitions <= 2):
        raise InputError('repetitions must be 1 or 2')

    instances = []
    seen_ids = set()
    for data in scenarios:
        s = validate_scenario(data)
        if s['schema_version'] != 2:
            raise InputError('scale campaign requires road-backed v2 scenarios')
        witness = proposal(s, s['generation']['witness_routes'])
        rep = validate_solution(s, witness)
        if not rep['feasible']:
            raise InputError('scenario witness is not feasible')
        m = rep['metrics']
        inst_id = f"{s['dataset']['city_id']}-{len(s['stops'])}s-{len(s['vehicle_ids'])}v-{s['generation']['seed']}"
        if inst_id in seen_ids:
            raise InputError(f'duplicate instance id: {inst_id}')
        seen_ids.add(inst_id)
        s_copy = copy.deepcopy(s)
        s_copy['id'] = inst_id
        witness = proposal(s_copy, s_copy['generation']['witness_routes'])
        rep = validate_solution(s_copy, witness)
        instances.append(dict(
            id=inst_id,
            scenario=s_copy,
            scenario_hash=scenario_hash(s_copy),
            reference=dict(solution=witness, validation=rep, proof_status='not_certified'),
            scales=copy.deepcopy(s_copy['objective']),
            hypervolume_reference=[max(1, 2 * m['fleet_elapsed_s']), max(1, 2 * m['estimated_fuel_l'])],
        ))

    cases = []
    for instance in instances:
        for budget in budgets:
            for rep_idx in range(repetitions):
                for method in methods:
                    for weight in weights:
                        cases.append(dict(
                            instance=instance['id'],
                            method=method,
                            budget_s=budget,
                            repetition=rep_idx,
                            seed=rep_idx,
                            time_weight=weight,
                            recording=True,
                            control=False,
                        ))
    random.Random(42).shuffle(cases)
    for index, case in enumerate(cases):
        case['id'] = f'case-{index:03d}'

    training_meta = {}
    checkpoint_hash = None
    if checkpoint_model is not None:
        from .learning import Policy
        Policy(checkpoint_model)
        checkpoint_hash = checkpoint_model['model_hash']
        training_meta = {k: v for k, v in checkpoint_model.get('metadata', {}).items() if k not in ('history',)}

    manifest = dict(
        schema_version=3,
        kind='experiment_manifest',
        design='m6-scale-ladder',
        planning_seed=42,
        main_cases=len(cases),
        control_cases=0,
        instances=instances,
        cases=cases,
        checkpoint=checkpoint_model,
        checkpoint_hash=checkpoint_hash,
        training=training_meta,
        provenance=provenance(),
        front_budget_multiplier=len(weights),
        scope='Descriptive M6 scale-ladder campaign across configurable stop counts, fleet sizes and service modes; no city-level generalization',
    )
    manifest['manifest_hash'] = scenario_hash(manifest)
    return manifest


def validate_manifest(manifest):
    if manifest.get('schema_version') == 2:
        from .learn_evaluate import validate_manifest_v2
        return validate_manifest_v2(manifest)
    if manifest.get('schema_version') == 3:
        expected = manifest.get('manifest_hash')
        unsigned = {k: v for k, v in manifest.items() if k != 'manifest_hash'}
        if expected != scenario_hash(unsigned):
            raise InputError('manifest hash mismatch')
        if manifest.get('kind') != 'experiment_manifest' or manifest.get('design') != 'm6-scale-ladder':
            raise InputError('invalid M6 scale-ladder manifest design')
        if not manifest.get('instances') or not manifest.get('cases'):
            raise InputError('M6 manifest requires non-empty instances and cases')
        if any(c.get('method') == 'learned' for c in manifest['cases']) and not manifest.get('checkpoint'):
            raise InputError('learned cases in M6 manifest require an embedded checkpoint')
        if manifest.get('checkpoint') is not None:
            from .learning import Policy
            Policy(manifest['checkpoint'])
        for inst in manifest['instances']:
            s = validate_scenario(inst['scenario'])
            if scenario_hash(s) != inst['scenario_hash']:
                raise InputError('instance scenario_hash mismatch')
        return manifest
    check(manifest, MANIFEST_V1)
    expected = manifest.get('manifest_hash')
    unsigned = {k:v for k,v in manifest.items() if k != 'manifest_hash'}
    if expected != scenario_hash(unsigned): raise InputError('manifest hash mismatch')
    rebuilt = freeze([i['scenario'] for i in manifest['instances']])
    for key in ('cases', 'instances', 'main_cases', 'control_cases', 'planning_seed', 'design', 'schema_version', 'kind'):
        if manifest[key] != rebuilt[key]: raise InputError(f'invalid frozen pilot {key}')
    return manifest


def compatible(a, b):
    return (a['instance'], a['budget_s'], a['time_weight'], a['recording']) == (b['instance'], b['budget_s'], b['time_weight'], b['recording'])


def nondominated(items):
    result = []
    for item in items:
        point = item['validation']['metrics']
        x, y = point['fleet_elapsed_s'], point['estimated_fuel_l']
        if any(v['validation']['metrics']['fleet_elapsed_s'] <= x and
               v['validation']['metrics']['estimated_fuel_l'] <= y for v in result): continue
        result = [v for v in result if not (x <= v['validation']['metrics']['fleet_elapsed_s'] and
                                            y <= v['validation']['metrics']['estimated_fuel_l'])]
        result.append(item)
    return result


def hypervolume(items, reference, scales):
    points = sorted((v['validation']['metrics']['fleet_elapsed_s'], v['validation']['metrics']['estimated_fuel_l']) for v in nondominated(items))
    area, ceiling = 0, reference[1]
    for x, y in points:
        if x < reference[0] and y < ceiling:
            area += (reference[0]-x)*(ceiling-y)
            ceiling = y
    return area / scales['time_scale_s'] / scales['fuel_scale_l']


def case_summary(result):
    """Compact derived view; the original independently validated run stays intact."""
    value={k:result[k] for k in ('run_id','case','status','error','termination_reason','observations','quality','timing','objective','learned','checkpoint_hash','training','alternatives','scenario_ref') if k in result}
    value.update(schema_version=1,kind='experiment_case_summary',scenario_available='scenario' in result or 'scenario_ref' in result)
    return value


def execute_case(root, manifest, case, cancel):
    directory = Path(root); directory.mkdir(parents=True, exist_ok=True)
    process_started = time.monotonic()
    observations, alternatives = [], []
    observer_s = 0
    validation_elapsed = 0
    parent = os.getppid()
    def cancelled(): return cancel.is_set() or os.getppid() != parent
    try:
        if case['method'] == 'ortools':
            import ortools.constraint_solver.pywrapcp  # Import/startup is outside measured solve.
        started = time.monotonic()
        # Reading the frozen copy belongs to solve time.
        instance = next(i for i in manifest['instances'] if i['id']==case['instance'])
        s = load_json(directory.parent.parent/'scenarios'/f"{case['instance']}.json")
        original_hash = scenario_hash(s)
        if original_hash != instance['scenario_hash']:
            raise InputError('frozen scenario copy hash mismatch')
        s['objective'].update(time_weight=case['time_weight'], fuel_weight=1-case['time_weight'])
        def candidate(solution, validation, elapsed):
            nonlocal alternatives, observer_s, validation_elapsed
            validation_elapsed = elapsed
            before = time.monotonic()
            item = dict(solution=solution, validation=validation, elapsed_s=elapsed,
                        case_id=case['id'], original_scenario_hash=original_hash)
            timely = elapsed <= case['budget_s']
            alternatives = [a for a in alternatives if (a['elapsed_s'] <= case['budget_s']) != timely] + nondominated(
                [a for a in alternatives if (a['elapsed_s'] <= case['budget_s']) == timely] + [item])
            observer_s += time.monotonic()-before
        def observe(kind, payload, elapsed):
            nonlocal observer_s
            if kind != 'feasible_best': return
            before = time.monotonic()
            item = dict(elapsed_s=validation_elapsed, **payload)
            observations.append(item)
            if case['recording']:
                with (directory/'events.jsonl').open('a') as stream:
                    event=dict(schema_version=1,job_id=directory.parent.parent.name+'/'+case['id'],seq=len(observations),
                               elapsed_s=validation_elapsed,kind='feasible_best',payload=payload)
                    stream.write(json.dumps(event, allow_nan=False)+'\n'); stream.flush(); os.fsync(stream.fileno())
            observer_s += time.monotonic()-before
        result = run_solver(SolverRequest(s, case['method'], case['budget_s'], case['seed'], observe,
                                          cancelled, candidate, time.monotonic()-started,
                                          str(directory.parent.parent/'checkpoint.json') if case['method']=='learned' else None))
        if case['method']=='learned' and result['learned']['checkpoint_hash']!=manifest['checkpoint_hash']:
            raise InputError('runtime checkpoint hash mismatch')
        solve_s = time.monotonic()-started
        result.update(schema_version=3, kind='experiment_run', case=case, original_scenario_hash=original_hash,
                      scenario_hash=scenario_hash(s), scenario=s, objective=s['objective'],
                      comparison_family='time' if case['time_weight']==1 else 'time_fuel',
                      analysis_membership=(['time'] if case['method']=='ortools' else
                                           ['time','time_fuel'] if case['time_weight']==1 else ['time_fuel']),
                      reference=instance['reference'], observations=observations if case['recording'] else [],
                      alternatives=alternatives, manifest_hash=manifest['manifest_hash'], provenance=provenance())
        result['timing'].update(solve_s=solve_s, observer_s=observer_s,
                               overrun_s=max(0, solve_s-case['budget_s']),
                               process_setup_s=started-process_started, shared_road_preparation_s=s['preparation_s'])
        result['peak_memory_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Quality accounting stays available in controls without event persistence.
        result['quality'] = summarize_case(s, case, observations, instance['reference'])
        result['status'] = 'cancelled' if cancelled() else 'completed'
        if manifest['schema_version'] in (2, 3):
            result.update(schema_version=4, scenario_ref=f"scenarios/{case['instance']}.json",
                          checkpoint_hash=manifest.get('checkpoint_hash') if case['method']=='learned' else None,
                          training=manifest.get('training', {}))
            result.pop('scenario', None)
            result.pop('reference', None)
    except Exception as exc:
        result = dict(schema_version=4 if manifest['schema_version'] in (2, 3) else 3, kind='experiment_run', case=case, status='failed',
                      termination_reason='adapter_error', error=f'{type(exc).__name__}: {exc}',
                      observations=observations if case['recording'] else [], alternatives=alternatives)
    result['run_id'] = directory.parent.parent.name+'/'+case['id']
    from .contracts_v4 import RUN_V4
    check(result, RUN_V4 if result['schema_version']==4 else RUN_V3)
    before = time.monotonic()
    write_json_atomic(directory/'result.json', result)
    write_json_atomic(directory/'summary.json', case_summary(result))
    write_json_atomic(directory/'publication.json', dict(publication_s=time.monotonic()-before, child_wall_s=time.monotonic()-process_started))


def summarize_case(s, case, observations, reference):
    timely = [v for v in observations if v['elapsed_s'] <= case['budget_s']]
    target = objective_score(s, reference['validation'])
    reached = next((v['elapsed_s'] for v in timely if objective_score(s, v['validation']) <= target), None)
    return dict(feasible_within_budget=bool(timely), first_feasible_s=timely[0]['elapsed_s'] if timely else None,
                target_time_s=reached if reached is not None else case['budget_s'], target_censored=reached is None,
                best_score=min((objective_score(s, v['validation']) for v in timely), default=None),
                best_metrics=timely[-1]['validation']['metrics'] if timely else None)


def campaign(jobs, id, manifest):
    validate_manifest(manifest)
    directory = jobs.root/id; directory.mkdir(exist_ok=True)
    write_json_atomic(directory/'manifest.json', manifest)
    if manifest['schema_version'] == 2:
        from .learn_evaluate import archive_inputs
        archive_inputs(directory,manifest)
    else:
        if manifest.get('checkpoint') is not None:
            write_json_atomic(directory/'checkpoint.json', manifest['checkpoint'])
        for instance in manifest['instances']:
            write_json_atomic(directory/'scenarios'/f"{instance['id']}.json", instance['scenario'])
    for source in Path('engine').glob('*.py'):
        destination=directory/'sources'/source
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(source.read_bytes())
    for source in [*Path('.').glob('requirements*.lock'), Path('client/package-lock.json'), Path('source-provenance.json')]:
        if source.exists():
            destination=directory/'sources'/source
            destination.parent.mkdir(parents=True,exist_ok=True); destination.write_bytes(source.read_bytes())
    context = multiprocessing.get_context('spawn')
    campaign_started = time.monotonic()
    for case in manifest['cases']:
        if jobs.get(id)['cancel_requested'] or (manifest.get('deadline_monotonic_s') is not None and time.monotonic() >= manifest['deadline_monotonic_s']): break
        cancel = context.Event()
        process = context.Process(target=execute_case, args=(str(directory/'cases'/case['id']), manifest, case, cancel), daemon=True)
        before = time.monotonic(); process.start()
        while process.is_alive():
            if jobs.get(id)['cancel_requested'] or (manifest.get('deadline_monotonic_s') is not None and time.monotonic() >= manifest['deadline_monotonic_s']): cancel.set()
            process.join(.1)
        path = directory/'cases'/case['id']
        if not (path/'result.json').exists():
            failed=dict(schema_version=4 if manifest['schema_version'] in (2, 3) else 3,kind='experiment_run',case=case,status='failed', termination_reason='process_error', exitcode=process.exitcode)
            write_json_atomic(path/'result.json',failed)
            write_json_atomic(path/'summary.json',case_summary(failed))
        wall = time.monotonic()-before
        publication = load_json(path/'publication.json') if (path/'publication.json').exists() else {}
        write_json_atomic(path/'process.json', dict(process_wall_s=wall, started_monotonic_s=before, finished_monotonic_s=before+wall,
                          startup_import_ipc_s=max(0, wall-publication.get('child_wall_s',wall)),
                          exitcode=process.exitcode, pid=process.pid))
        jobs.emit(id, 'metric', dict(case_id=case['id'], completed=len(list((directory/'cases').glob('*/result.json'))), total=len(manifest['cases'])), time.monotonic()-campaign_started)
    return report(directory)


def report(directory):
    directory = Path(directory)
    manifest = validate_manifest(load_json(directory/'manifest.json'))
    compact = manifest['schema_version'] in (2, 3)
    results = [load_json(p) for p in sorted((directory/'cases').glob('*/summary.json' if compact else '*/result.json'))]
    methods = ('ordered','shuffled','learned') if compact else METHODS
    repetitions = sorted({c['repetition'] for c in manifest['cases']}) if manifest['schema_version'] == 3 else (range(2) if compact else range(3))
    budgets = sorted({c['budget_s'] for c in manifest['cases']}) if manifest['schema_version'] == 3 else (5, 15)
    rows, fronts = [], []
    for result in results:
        case = result['case']
        rows.append(dict(**case, status=result['status'], termination_reason=result['termination_reason'],
                         **result.get('quality', {}), solve_s=result.get('timing',{}).get('solve_s'),
                         observer_s=result.get('timing',{}).get('observer_s'),
                         fleet_minutes=(result.get('quality',{}).get('best_metrics') or {}).get('fleet_elapsed_s',0)/60 if result.get('quality',{}).get('best_metrics') else None,
                         estimated_fuel_l=(result.get('quality',{}).get('best_metrics') or {}).get('estimated_fuel_l')))
    if compact:
        for row,result in zip(rows,results):
            row.update(checkpoint_hash=result.get('checkpoint_hash'),inference_device=result.get('learned',{}).get('device','cpu'),
                       inference_s=result.get('learned',{}).get('inference_s',0),
                       training_device=manifest.get('training', {}).get('training_device'),
                       shared_training_s=manifest.get('training', {}).get('training_duration_s'))
    for instance in manifest['instances']:
        for method in (methods if compact else ('ordered', 'shuffled')):
            for budget in budgets:
                for repetition in repetitions:
                    selected = [r for r in results if r['case']['instance']==instance['id'] and r['case']['method']==method and r['case']['budget_s']==budget and r['case']['repetition']==repetition and not r['case']['control']]
                    if not selected and manifest['schema_version'] == 3:
                        continue
                    alternatives = nondominated([a for r in selected for a in r.get('alternatives',[]) if a['elapsed_s'] <= budget])
                    fronts.append(dict(instance=instance['id'],method=method,budget_s=budget,repetition=repetition,
                                       nominal_front_budget_s=3*budget, alternatives=alternatives,
                                       hypervolume=hypervolume(alternatives,instance['hypervolume_reference'],instance['scales'])))
    pairs = []
    for a in rows:
        for b in rows:
            if methods.index(a['method']) < methods.index(b['method']) and a['repetition']==b['repetition'] and compatible(a,b):
                x, y = a.get('best_score'), b.get('best_score')
                pairs.append(dict(a=a['id'], b=b['id'], method_a=a['method'], method_b=b['method'],
                                  pairing='temporal_repetition' if 'ortools' in (a['method'],b['method']) else 'custom_seed',
                                  b_minus_a=None if x is None or y is None else y-x))
    controls = []
    for a in rows:
        if not a['control']: continue
        b = next((b for b in rows if not b['control'] and all(a[k]==b[k] for k in ('instance','method','budget_s','repetition','time_weight'))),None)
        if b:
            x,y=a.get('best_score'),b.get('best_score')
            controls.append(dict(control=a['id'],recorded=b['id'],quality_delta=None if x is None or y is None else y-x,
                                 observer_s_recorded=b.get('observer_s'),observer_s_control=a.get('observer_s')))
    variability = []
    groups = {}
    for row in rows:
        key = tuple(row[k] for k in ('instance','method','budget_s','time_weight','recording'))
        groups.setdefault(key, []).append(row)
    for key, group in groups.items():
        scores = [r['best_score'] for r in group if r.get('best_score') is not None]
        variability.append(dict(zip(('instance','method','budget_s','time_weight','recording'),key),
                                repetitions=len(group), feasible=sum(r.get('feasible_within_budget',False) for r in group),
                                failures=sum(r['status']=='failed' for r in group),
                                target_censored=sum(r.get('target_censored',True) for r in group),
                                conditional_score_mean=statistics.mean(scores) if scores else None,
                                conditional_score_stdev=statistics.stdev(scores) if len(scores)>1 else None,
                                conditional_score_min=min(scores) if scores else None,
                                conditional_score_max=max(scores) if scores else None))
    curves = [dict(case_id=r['case']['id'],points=[dict(elapsed_s=o['elapsed_s'],
                   score=objective_score(dict(objective=r['objective']),o['validation']), metrics=o['validation']['metrics'],
                   within_budget=o['elapsed_s']<=r['case']['budget_s']) for o in r.get('observations',[])]) for r in results if r.get('objective')]
    value = dict(schema_version=2 if compact else 1,kind='experiment_report',manifest_hash=manifest['manifest_hash'],
                 completed=len(results),total=len(manifest['cases']),rows=rows,fronts=fronts,paired_differences=pairs,
                 recording_controls=controls,variability=variability,anytime_curves=curves,scope=manifest['scope'],reference_status='feasible_not_certified')
    if compact:
        if (directory/'training-cost.json').exists():
            costs=load_json(directory/'training-cost.json')
            if costs['checkpoint_hash']!=manifest['checkpoint_hash']: raise InputError('training cost ledger checkpoint mismatch')
            value['training_cost']=costs
            for row in rows:
                row.update(shared_preflight_s=costs['preflight_s'],shared_training_and_preflight_s=costs['training_and_preflight_s'])
        value.update(checkpoint_hash=manifest.get('checkpoint_hash'),training=manifest.get('training', {}),
                     unexecuted=[c['id'] for c in manifest['cases'] if c['id'] not in {r['case']['id'] for r in results}],
                     campaign_complete=(load_json(directory/'audit.json').get('campaign_complete',False)
                                        if (directory/'audit.json').exists() and len(results)==336 else (len(results)==len(manifest['cases']) and manifest['schema_version']==3)))
    from .contracts_v4 import REPORT_V2
    if manifest['schema_version'] in (1, 2):
        check(value, REPORT_V2 if manifest['schema_version'] == 2 else REPORT_V1)
    write_json_atomic(directory/'report.json',value,overwrite=True)
    fields = sorted({k for row in rows for k in row})
    output = io.StringIO(); writer=csv.DictWriter(output,fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    (directory/'report.csv').write_text(output.getvalue())
    text = ('# M4 held-out comparison\n\n' if compact else '# M3 descriptive pilot\n\n')+manifest['scope']+'\n\nWitness references are feasible, not certified optima. Fuel values are uncalibrated model estimates in liters.\n\n'
    if value.get('training_cost'):
        costs=value['training_cost']
        text += f"Shared training: {costs.get('training_phase_s',costs['model_training_s']):.6f} s (measured training {costs['model_training_s']:.6f} s); preflight containers: {costs['preflight_s']:.6f} s; total: {costs['training_and_preflight_s']:.6f} s. The in-training GPU probe is already included.\n\n"
    text += f"Completed cases: {len(results)}/{len(manifest['cases'])}. Fronts cost three preference budgets. Late validations excluded from budget quality. Target failures are censored. No city-level confidence intervals.\n\n"
    text += '| Case | Instance | Method | Budget s | Status | Feasible in budget | Target censored | Score |\n|---|---|---|---:|---|---|---|---:|\n'
    for r in rows: text += f"| {r['id']} | {r['instance']} | {r['method']} | {r['budget_s']} | {r['status']} | {r.get('feasible_within_budget')} | {r.get('target_censored')} | {r.get('best_score')} |\n"
    text += '\nVariability (conditional feasible scores, descriptive only):\n\n'
    for group in variability:
        text += '- ' + json.dumps(group) + '\n'
    text += '\nPaired differences and recording controls:\n\n'
    for pair in pairs + controls:
        text += '- ' + json.dumps(pair) + '\n'
    (directory/'report.md').write_text(text)
    return value
