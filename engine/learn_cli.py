"""Command orchestration and bounded, durable M4 phase supervision."""
from pathlib import Path
import time

from .contracts import InputError, load_json
from .jobs import Jobs
from .storage import write_json_atomic


def campaign(dataset_path,root,try_gpu=True):
    from .worker import Worker
    from .learn_evaluate import freeze, audit
    root=Path(root).resolve(); root.mkdir(parents=True,exist_ok=True)
    started=time.monotonic(); overall=started+110*60  # Reserve preparation/transfer time.
    receipt_path=Path(dataset_path).parent/'preparation.json'
    preparation=load_json(receipt_path) if receipt_path.exists() else None
    if preparation is not None:
        elapsed=time.time()-preparation['started_unix_s']
        if elapsed<0 or elapsed>600: raise InputError('preparation/transfer exceeded its ten-minute phase limit')
        overall=min(overall,started+120*60-elapsed)
    status_path=root/'campaign-status.json'
    status=dict(schema_version=1,kind='learned_campaign_status',status='running',phase='collection',started_unix_s=time.time(),
                phase_limits_s=dict(preparation=600,collection=1500,training=600,evaluation=3900,audit=600),preparation=preparation)
    def publish(): write_json_atomic(status_path,status,overwrite=True)
    def cancelled(): return (root/'campaign.cancel').exists()
    def run_phase(worker,kind,input_,output,seconds):
        if cancelled() or time.monotonic()>=overall: raise RuntimeError('global deadline or cancellation')
        deadline=min(overall,time.monotonic()+seconds)
        status.update(phase=kind,phase_started_unix_s=time.time(),phase_deadline_unix_s=time.time()+deadline-time.monotonic()); publish()
        job=worker.jobs.submit(kind,dict(input=str(input_),output=str(output),deadline=deadline,try_gpu=try_gpu))
        status['job_id']=job['id']; publish()
        # Cancellation is observed by the worker through its queue flag, set by remote cancel.
        worker.execute_one()
        result=worker.jobs.get(job['id'])
        if result['status']!='completed': raise RuntimeError(f"{kind} {result['status']}: {result.get('error')}")
        return worker.jobs.result(job['id'])
    publish()
    try:
        with_worker=Worker(root/'jobs').acquire()
        try:
            collection=run_phase(with_worker,'collect',dataset_path,root/'samples',25*60)
            training=run_phase(with_worker,'train',root/'samples'/'collection.json',root/'training',10*60)
            if cancelled(): raise RuntimeError('campaign cancelled before held-out freeze')
            deadline=min(overall,time.monotonic()+65*60)
            status.update(phase='evaluation',phase_started_unix_s=time.time(),phase_deadline_unix_s=time.time()+deadline-time.monotonic()); publish()
            manifest=freeze(dataset_path,root/'training'/'checkpoint.json',root/'frozen',deadline)
            job=with_worker.jobs.submit('experiment',dict(manifest=manifest)); status['job_id']=job['id']; status['evaluation_job_id']=job['id']; publish()
            with_worker.execute_one()
            status.update(phase='audit'); publish()
            audited=audit(with_worker.jobs.root/job['id'],min(overall,time.monotonic()+10*60))
            status.update(status='completed' if audited['campaign_complete'] else 'incomplete',audit=audited)
        finally: with_worker.close()
    except Exception as exc:
        status.update(status='cancelled' if cancelled() else 'failed',error=f'{type(exc).__name__}: {exc}')
    status['duration_s']=time.monotonic()-started; publish()
    return status


def execute(args):
    from .learn_data import dataset,collect
    from .learn_train import train
    from .learn_evaluate import freeze,audit
    if args.action=='dataset':
        if not args.output: raise InputError('dataset requires --output directory')
        return dataset(args.output,args.data_root,deadline=time.monotonic()+600)
    if not args.input: raise InputError(f'{args.action} requires an input path')
    if args.action=='audit': return audit(args.input)
    if args.action=='campaign': return campaign(args.input,args.root,not args.cpu)
    jobs=Jobs(args.root)
    if args.action in ('collect','train'):
        if not args.output: raise InputError('phase requires --output directory')
        return jobs.submit(args.action,dict(input=str(Path(args.input).resolve()),output=str(Path(args.output).resolve()),try_gpu=not args.cpu))
    if not args.output or not args.checkpoint: raise InputError('evaluate requires --checkpoint and --output frozen directory')
    manifest=freeze(args.input,args.checkpoint,args.output,time.monotonic()+65*60)
    return jobs.submit('experiment',dict(manifest=manifest))
