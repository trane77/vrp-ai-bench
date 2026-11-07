"""Real-road acceptance; requires prepared cities, OSRM, HTTP service and worker.
No mocked provider and no external routing fallback. Results stay under runs/.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.contracts import validate_scenario
from engine.validation import validate_solution


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--count',type=int,default=20)
    parser.add_argument('--budget-s',type=float,default=5)
    parser.add_argument('--report',default='runs/m2-acceptance.json')
    args=parser.parse_args()
    def api(path,body=None):
        request=urllib.request.Request(args.url+'/api/v1'+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
    def await_job(id):
        end=time.monotonic()+180
        while time.monotonic()<end:
            job=api('/jobs/'+id)
            if job['status'] in ('completed','failed','cancelled'):
                if job['status']!='completed':raise RuntimeError(job)
                return job
            time.sleep(.2)
        raise TimeoutError('job did not finish; is the separate worker running?')
    cities=api('/cities')
    reports=[]
    for city_id in ('bologna','modena'):
        if not next(c for c in cities if c['id']==city_id)['installed']:raise RuntimeError(city_id+' is not installed')
        draft=api('/drafts',dict(city_id=city_id,seed=42,count=args.count))
        base=dict(draft_id=draft['id'],revision=draft['revision'])
        prep=api('/jobs',dict(kind='prepare',**base));await_job(prep['id'])
        scenario=validate_scenario(api('/jobs/'+prep['id']+'/result'))
        if scenario['dataset']['city_id']!=city_id:raise RuntimeError('mixed-city result')
        run=api('/jobs',dict(kind='solve',prepared_job_id=prep['id'],budget_s=args.budget_s,**base))
        job=await_job(run['id']);result=api('/jobs/'+run['id']+'/result')
        if result['solution'] is None:raise RuntimeError('demo has no feasible solution: '+city_id)
        validation=validate_solution(scenario,result['solution'])
        if not validation['feasible'] or validation!=result['validation']:raise RuntimeError('invalid saved solution')
        snapshot=api('/jobs/'+run['id']+'/snapshot')
        if snapshot['best']['validation']!=validation or snapshot['best']['solution']!=result['solution']:
            raise RuntimeError('terminal replay differs from saved result')
        # New HTTP request/session reads the same persisted artifacts.
        if api('/jobs/'+run['id']+'/result')!=result:raise RuntimeError('result changed on reopen')
        record=dict(city_id=city_id,prepare_job_id=prep['id'],run_job_id=run['id'],
            dataset=scenario['dataset'],timing=result['timing'],metrics=validation['metrics'],
            runtime=result['runtime'],termination=job['termination_reason'],proof_status=result['proof_status'],
            snapping_rejections=scenario['generation']['rejected_samples'],preparation_s=scenario['preparation_s'])
        print(json.dumps(record),flush=True);reports.append(record)
    Path(args.report).parent.mkdir(parents=True,exist_ok=True)
    Path(args.report).write_text(json.dumps(reports,indent=2)+'\n')


if __name__=='__main__':main()
