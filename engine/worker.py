"""Separate single-worker process. Request-server lifetime is irrelevant."""
import fcntl
import json
from pathlib import Path
import time

from .city import CityRegistry, verify_service
from .contracts import InputError
from .drafts import prepare_draft
from .jobs import Jobs, Conflict
from .routing import OSRM, Cancelled
from .solver import solve, scenario_hash
from .storage import write_json_atomic


class Worker:
    def __init__(self,root='runs',data_root='data',provider_factory=OSRM):
        self.jobs=Jobs(root); self.registry=CityRegistry(data_root)
        self.provider_factory=provider_factory
        self.lock=None

    def acquire(self):
        self.lock=(self.jobs.root/'worker.lock').open('a')
        try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close(); self.lock=None
            raise RuntimeError('another worker already owns this runs directory')
        (self.jobs.root/'worker.stop').unlink(missing_ok=True)
        self.jobs.recover()
        return self

    def close(self):
        if self.lock:
            self.lock.close(); self.lock=None

    def execute_one(self):
        if self.lock is None: raise RuntimeError('worker lock required')
        job=self.jobs.claim()
        if job is None: return False
        id=job['id']; request=job['request']; started=time.monotonic(); last_metric=-1; omitted=0
        def cancelled(): return self.jobs.get(id)['cancel_requested']
        def emit(kind,payload,elapsed=None):
            nonlocal last_metric,omitted
            now=time.monotonic()-started
            if kind=='metric':
                if now-last_metric<1:
                    omitted+=1; return
                last_metric=now
            self.jobs.emit(id,kind,payload,elapsed if elapsed is not None else now)
        result=None
        try:
            if cancelled(): raise Cancelled('cancelled before computation')
            if job['kind']=='experiment':
                from .experiments import campaign
                result=campaign(self.jobs,id,request['manifest'])
                status='cancelled' if cancelled() else ('completed' if result['completed']==result['total'] else 'failed')
                reason='cancelled' if cancelled() else ('campaign_finished' if status=='completed' else 'campaign_incomplete')
                feasibility='unknown'
            elif job['kind'] in ('collect','train'):
                if job['kind']=='collect':
                    from .learn_data import collect
                    timeout_s = request.get('timeout_s')
                    deadline = request.get('deadline', time.monotonic() + (timeout_s if timeout_s else 1500))
                    result=collect(request['input'],request['output'],deadline=deadline,
                                   cancelled=cancelled,version=request.get('version',1),timeout_s=timeout_s)
                else:
                    from .learn_train import train
                    result=train(request['input'],request['output'],deadline=request.get('deadline',time.monotonic()+600),
                                 cancelled=cancelled,try_gpu=request.get('try_gpu',True),
                                 epochs=request.get('epochs',50),version=request.get('version'))
                status='completed' if result.get('status')=='completed' else 'failed'
                reason='phase_finished' if status=='completed' else 'phase_incomplete'
                feasibility='unknown'
            elif job['kind']=='prepare':
                draft=self.jobs.draft(request['draft_id'])
                # The queued request embeds the immutable revision, even if the editor changes later.
                data=request['draft']
                manifest=self.registry.get(data['city_id'])
                if manifest['manifest_hash']!=data['dataset_hash']: raise InputError('installed dataset changed')
                if self.provider_factory is OSRM:
                    import os
                    if manifest['manifest_hash'] not in os.environ.get('VRP_OSRM_EXTERNAL_MANIFEST_HASHES','').split(','):
                        verify_service(self.registry,manifest)
                result=prepare_draft(data,manifest,self.provider_factory(cancelled=cancelled),emit,cancelled)
                emit('prepared',dict(scenario_hash=scenario_hash(result),preparation_s=result['preparation_s']))
                status='completed'; reason='prepared'; feasibility='unknown'
            else:
                prepared=self.jobs.result(request['prepared_job_id'])
                if scenario_hash(prepared)!=request['scenario_hash']: raise Conflict('prepared scenario changed')
                policy=request.get('policy') or 'cpu'
                if policy not in ('cpu','learned'):
                    raise InputError(f'unsupported policy: {policy!r}')
                if policy=='learned':
                    # Locate the most recently imported M4 checkpoint in runs/m4-imports/.
                    import os
                    checkpoint_env=os.environ.get('VRP_LEARNED_CHECKPOINT','')
                    if checkpoint_env:
                        checkpoint=checkpoint_env
                    else:
                        # Walk m4-imports for checkpoint.json files and pick the newest.
                        candidates=[
                            *self.jobs.root.glob('m8-models/*/checkpoint.json'),
                            *self.jobs.root.parent.glob('m8-models/*/checkpoint.json'),
                            *self.jobs.root.glob('m6-models/*/checkpoint.json'),
                            *self.jobs.root.parent.glob('m6-models/*/checkpoint.json'),
                            *self.jobs.root.glob('m4-imports/*/checkpoint.json'),
                            *self.jobs.root.parent.glob('m4-imports/*/checkpoint.json'),
                            *self.jobs.root.glob('m4/training/checkpoint.json'),
                            *self.jobs.root.parent.glob('m4/training/checkpoint.json'),
                        ]
                        if not candidates:
                            raise InputError(
                                'learned policy requested but no checkpoint found; '
                                'set VRP_LEARNED_CHECKPOINT or run "python -m engine remote fetch"')
                        checkpoint=str(max(candidates,key=lambda p: p.stat().st_mtime))
                    result=solve(prepared,seed=request['seed'],budget_s=request['budget_s'],
                        max_evaluations=request['max_evaluations'],restarts=request['restarts'],
                        observe=emit,cancelled=cancelled,
                        selection_policy='learned',checkpoint=checkpoint)
                else:
                    result=solve(prepared,seed=request['seed'],budget_s=request['budget_s'],
                        max_evaluations=request['max_evaluations'],restarts=request['restarts'],
                        observe=emit,cancelled=cancelled)
                result['run_id']=id
                status=result['job_status']; reason=result['termination_reason']; feasibility=result['feasibility']
            if cancelled() and status!='cancelled':
                status='cancelled'; reason='cancelled'
                if job['kind']=='solve':
                    result['job_status']='cancelled'; result['termination_reason']='cancelled'
            filename=f'{id}/result.json'
            write_json_atomic(self.jobs.root/filename,result)
            best=(dict(solution=result['solution'],validation=result['validation'],partial=False)
                  if job['kind']=='solve' and result.get('solution') else None)
            emit('terminal',dict(status=status,reason=reason,feasibility=feasibility,best=best,
                                 optional_samples_omitted=omitted,trace_mode='accepted_changes'))
            self.jobs.finish(id,status,reason,feasibility,result=filename)
        except Cancelled:
            emit('terminal',dict(status='cancelled',reason='cancelled',best=None,optional_samples_omitted=omitted))
            self.jobs.finish(id,'cancelled','cancelled')
        except Exception as exc:
            # Preserve the diagnostic locally; return a bounded message rather than a raw traceback to UI.
            message=f'{type(exc).__name__}: {str(exc)[:400]}'
            try: emit('terminal',dict(status='failed',reason='execution_error',message=message,best=None))
            except Exception: pass  # Disk failure must still attempt to mark metadata failed.
            self.jobs.finish(id,'failed','execution_error',error=message)
        return True

    def run(self):
        self.acquire()
        import signal
        stop_requested = False
        def _on_signal(signum, frame):
            nonlocal stop_requested
            stop_requested = True
        old_sigterm = signal.signal(signal.SIGTERM, _on_signal)
        old_sigint = signal.signal(signal.SIGINT, _on_signal)
        try:
            while not stop_requested and not (self.jobs.root/'worker.stop').exists():
                if not self.execute_one(): time.sleep(.25)
        finally:
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)
            (self.jobs.root/'worker.stop').unlink(missing_ok=True)
            self.close()
