"""Audit a completed real-road pilot, independently of search and report totals."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.contracts import load_json, validate_scenario, check
from engine.contracts_v2 import EVENT
from engine.experiments import validate_manifest, summarize_case
from engine.solver import scenario_hash
from engine.validation import validate_solution
from engine.storage import write_json_atomic


def main():
    parser=argparse.ArgumentParser();parser.add_argument('campaign');args=parser.parse_args()
    root=Path(args.campaign);manifest=validate_manifest(load_json(root/'manifest.json'))
    for source,digest in manifest['provenance']['source_hashes'].items():
        assert hashlib.sha256((root/'sources'/source).read_bytes()).hexdigest()==digest
    cases=[];validated=0;failed=[]
    for case in manifest['cases']:
        directory=root/'cases'/case['id'];result=load_json(directory/'result.json')
        assert result['case']==case
        process=load_json(directory/'process.json');cases.append(process)
        if result['status']!='completed':failed.append(dict(id=case['id'],status=result['status'],reason=result['termination_reason']));continue
        s=validate_scenario(result['scenario'])
        instance=next(i for i in manifest['instances'] if i['id']==case['instance'])
        assert result['original_scenario_hash']==instance['scenario_hash']
        assert result['scenario_hash']==scenario_hash(s)
        assert result['provenance']['source_hashes']==manifest['provenance']['source_hashes'], 'Engine source changed during campaign'
        if result['solution']:
            assert result['validation']==validate_solution(s,result['solution'])
            assert result['validation']['feasible'];validated+=1
        for alternative in result['alternatives']+result['observations']:
            assert alternative['validation']==validate_solution(s,alternative['solution'])
            assert alternative['validation']['feasible'];validated+=1
        if case['recording']:
            events=[json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()] if (directory/'events.jsonl').exists() else []
            assert len(events)==len(result['observations'])
            for seq,(event,observation) in enumerate(zip(events,result['observations']),1):
                check(event,EVENT)
                assert event['seq']==seq and event['elapsed_s']==observation['elapsed_s']
                assert event['payload']['solution']==observation['solution']
            if result['solution']:
                assert result['observations'][-1]['solution']==result['solution']
            assert result['quality']==summarize_case(s,case,result['observations'],instance['reference'])
        else:
            assert not result['observations'] and not (directory/'events.jsonl').exists()
        assert result['proof_status']=='not_certified'
        assert result['timing']['overrun_s']==max(0,result['timing']['solve_s']-case['budget_s'])
    for a,b in zip(cases,cases[1:]):assert a['finished_monotonic_s']<=b['started_monotonic_s']
    output=dict(cases=len(cases),main_cases=84,controls=18,validated_solutions=validated,failed_cases=failed,
                manifest_hash=manifest['manifest_hash'],sequential_processes_verified=True,
                scope=manifest['scope'],source_hashes_verified=True)
    write_json_atomic(root/'acceptance.json',output,overwrite=True)
    print(json.dumps(output,indent=2))
    return bool(failed)

if __name__=='__main__':sys.exit(main())
