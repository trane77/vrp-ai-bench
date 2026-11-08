"""Loopback HTTP/WebSocket facade; computation belongs to engine.worker."""
import asyncio
import hmac
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .city import CityRegistry
from .contracts import InputError
from .drafts import make_draft, validate_draft, draft_from_scenario
from .jobs import Jobs, Conflict, TERMINAL
from .solver import scenario_hash


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)

class NewExperiment(Strict):
    manifest: dict

class NewDraft(Strict):
    city_id: str
    seed: int=42
    count: int=Field(default=20,ge=1,le=100)

class EditDraft(Strict):
    revision: str
    data: dict

class NewJob(Strict):
    kind: str
    draft_id: str
    revision: str
    prepared_job_id: str | None=None
    seed: int=42
    budget_s: float=Field(default=10,ge=0,le=3600)
    max_evaluations: int | None=Field(default=None,ge=0)
    restarts: int=Field(default=8,ge=1,le=1000)
    policy: str | None=None  # "cpu" | "learned" | None (defaults to "cpu")


def same_origin(origin,host):
    try:
        parsed=urlparse(origin)
        return parsed.scheme in ('http','https') and parsed.netloc==host
    except ValueError: return False


def create_app(root='runs',data_root='data',client_dir='client/dist',token=None,allowed_origins=(),allowed_hosts=None,allowed_client_ips=()):
    app=FastAPI(title='VRP AI local laboratory',version='0.2.0')
    jobs=Jobs(root); registry=CityRegistry(data_root)
    app.state.jobs=jobs; app.state.registry=registry
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=allowed_hosts or ['127.0.0.1','localhost','[::1]'])

    def authorized(value):
        return token is None or (value is not None and hmac.compare_digest(value,token))

    def origin_allowed(origin,host):
        return same_origin(origin,host) or origin in allowed_origins

    @app.middleware('http')
    async def protect_origin(request:Request,call_next):
        if allowed_client_ips and (request.client is None or request.client.host not in allowed_client_ips):
            return JSONResponse(status_code=403,content={'detail':'client address rejected'})
        origin=request.headers.get('origin')
        if origin and not origin_allowed(origin,request.headers.get('host','')):
            return JSONResponse(status_code=403,content={'detail':'cross-origin request rejected'})
        if request.method=='OPTIONS' and origin in allowed_origins:
            return JSONResponse(status_code=204,content=None,headers={'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Headers':'Content-Type, X-VRP-Token','Access-Control-Allow-Methods':'GET, POST, PUT, OPTIONS'})
        if token is not None and not authorized(request.headers.get('x-vrp-token')):
            return JSONResponse(status_code=401,content={'detail':'invalid or missing token'})
        if request.headers.get('content-length','0').isdigit() and int(request.headers.get('content-length','0'))>32*1024*1024:
            return JSONResponse(status_code=413,content={'detail':'request exceeds 32 MiB'})
        response=await call_next(request)
        if origin in allowed_origins: response.headers['Access-Control-Allow-Origin']=origin
        return response

    @app.exception_handler(InputError)
    async def invalid(request,exc):
        return JSONResponse(status_code=409 if isinstance(exc,Conflict) else 422,content={'detail':str(exc)})

    @app.exception_handler(KeyError)
    async def missing(request,exc):
        return JSONResponse(status_code=404,content={'detail':str(exc)})

    def public_job(job):
        if job['kind']=='experiment':
            job=dict(job,request=dict(job['request']))
            manifest=job['request']['manifest']
            job['request']['manifest']={k:manifest[k] for k in ('manifest_hash','cases','main_cases','control_cases')}
        return job

    @app.get('/api/v1/health')
    def health(): return dict(service='ok',worker='separate process',schema_version=1)

    @app.get('/api/v1/cities')
    def cities(): return registry.list()

    @app.post('/api/v1/drafts',status_code=201)
    def new_draft(body:NewDraft):
        return jobs.save_draft(make_draft(registry.get(body.city_id),body.seed,body.count))

    @app.get('/api/v1/drafts/{id}')
    def get_draft(id:str): return jobs.draft(id)

    @app.put('/api/v1/drafts/{id}')
    def edit_draft(id:str,body:EditDraft):
        manifest=registry.get(body.data.get('city_id'))
        data=validate_draft(body.data,manifest)
        existing=jobs.draft(id)['data']
        def stop_shape(stops):
            return [(s['id'],s['coordinate'],s['packages'],s['mass_kg'],s['volume_m3']) for s in stops]
        if data['depot']!=existing['depot'] or stop_shape(data['stops'])!=stop_shape(existing['stops']):
            data['mode']='edited_demo'
        elif data['parameters']['vehicle_ids']!=existing['parameters']['vehicle_ids'] or all(
            s['window_open_s']==0 and s['window_close_s']==28800 for s in data['stops']):
            data['mode']='witness_demo'
        return jobs.save_draft(data,id,body.revision)

    @app.post('/api/v1/jobs',status_code=202)
    def new_job(body:NewJob):
        if body.policy not in (None,'cpu','learned'):
            raise InputError('policy must be "cpu", "learned", or omitted')
        draft=jobs.draft(body.draft_id)
        if draft['revision']!=body.revision: raise Conflict('draft revision is stale')
        request=body.model_dump()
        if body.kind=='prepare':
            validate_draft(draft['data'],registry.get(draft['data']['city_id']))
            request['draft']=draft['data']
        elif body.kind=='solve':
            if not body.prepared_job_id: raise InputError('prepared_job_id is required')
            prepared_job=jobs.get(body.prepared_job_id)
            if prepared_job['kind']!='prepare' or prepared_job['status']!='completed':
                raise Conflict('preparation has not completed')
            if (prepared_job['request']['draft_id']!=body.draft_id or
                prepared_job['request']['revision']!=body.revision):
                raise Conflict('prepared data belongs to another draft revision')
            scenario=jobs.result(body.prepared_job_id)
            request['scenario_hash']=scenario_hash(scenario)
        else: raise InputError('kind must be prepare or solve')
        return jobs.submit(body.kind,request)

    @app.get('/api/v1/jobs')
    def list_jobs(): return [public_job(j) for j in jobs.list()]

    @app.get('/api/v1/jobs/{id}')
    def get_job(id:str): return public_job(jobs.get(id))

    @app.post('/api/v1/jobs/{id}/cancel')
    def cancel(id:str): return public_job(jobs.cancel(id))

    @app.get('/api/v1/jobs/{id}/result')
    def result(id:str): return jobs.result(id)

    @app.get('/api/v1/jobs/{id}/editor')
    def editor(id:str):
        result=jobs.result(id)
        if jobs.get(id)['kind']!='prepare': raise InputError('editor requires a preparation result')
        return draft_from_scenario(result)

    @app.get('/api/v1/jobs/{id}/events')
    def events(id:str,after:int=0):
        if after<0: raise InputError('event cursor must be nonnegative')
        return jobs.events(id,after)

    @app.get('/api/v1/jobs/{id}/snapshot')
    def snapshot(id:str,before:int|None=None):
        if before is not None and before<0: raise InputError('snapshot cursor must be nonnegative')
        return jobs.snapshot(id,before)

    @app.websocket('/api/v1/jobs/{id}/stream')
    async def stream(socket:WebSocket,id:str,after:int=0):
        if ((allowed_client_ips and (socket.client is None or socket.client.host not in allowed_client_ips))
            or after<0 or not origin_allowed(socket.headers.get('origin',''),socket.headers.get('host',''))
            or not authorized(socket.query_params.get('token') if token is not None else None)):
            await socket.close(code=1008); return
        try: job=jobs.get(id)
        except KeyError:
            await socket.close(code=1008); return
        await socket.accept()
        cursor=after
        last_status=job['status']
        try:
            if job['last_seq']-cursor>500:
                snapshot=await asyncio.to_thread(jobs.snapshot,id)
                if snapshot and snapshot['seq']>cursor:
                    await socket.send_json(dict(type='snapshot',data=snapshot)); cursor=snapshot['seq']
            while True:
                batch=await asyncio.to_thread(jobs.events,id,cursor)
                if batch:
                    await socket.send_json(dict(type='events',data=batch)); cursor=batch[-1]['seq']
                job=await asyncio.to_thread(jobs.get,id)
                if job['status'] in TERMINAL and cursor>=job['last_seq']:
                    await socket.send_json(dict(type='status',data=job)); break
                if job['status']!=last_status and job['status'] not in TERMINAL:
                    await socket.send_json(dict(type='status',data=job)); last_status=job['status']
                await asyncio.sleep(.2)
        except (WebSocketDisconnect,RuntimeError): pass
        finally:
            try: await socket.close()
            except RuntimeError: pass

    @app.post('/api/v1/experiments',status_code=202)
    def new_experiment(body:NewExperiment):
        return public_job(jobs.submit('experiment',dict(manifest=body.manifest)))

    @app.get('/api/v1/experiments')
    def experiments(): return [public_job(j) for j in jobs.list() if j['kind']=='experiment']

    def experiment(id):
        job=jobs.get(id)
        if job['kind']!='experiment': raise InputError('experiment job required')
        return job

    @app.get('/api/v1/experiments/{id}')
    def experiment_status(id:str): return public_job(experiment(id))

    @app.post('/api/v1/experiments/{id}/cancel')
    def experiment_cancel(id:str):
        experiment(id)
        return public_job(jobs.cancel(id))

    @app.get('/api/v1/experiments/{id}/manifest')
    def experiment_manifest(id:str): return experiment(id)['request']['manifest']

    @app.get('/api/v1/experiments/{id}/report')
    def experiment_report(id:str):
        experiment(id)
        from .contracts import load_json
        path=jobs.root/id/'report.json'
        if not path.exists(): raise Conflict('report not yet published')
        return load_json(path)

    @app.get('/api/v1/experiments/{id}/export/{format}')
    def experiment_export(id:str,format:str):
        from fastapi.responses import FileResponse
        experiment(id)
        if format not in ('json','csv','md'): raise InputError('unsupported export')
        path=jobs.root/id/f'report.{format}'
        if not path.exists(): raise Conflict('report not yet published')
        return FileResponse(path,filename=path.name)

    @app.get('/api/v1/experiments/{id}/cases')
    def experiment_cases(id:str):
        job=experiment(id)
        return [dict(c, available=(jobs.root/id/'cases'/c['id']/'result.json').exists()) for c in job['request']['manifest']['cases']]

    @lru_cache(maxsize=16)
    def allowed_case_ids(id):
        return frozenset(c['id'] for c in experiment(id)['request']['manifest']['cases'])

    def case_path(id,case_id):
        if case_id not in allowed_case_ids(id): raise KeyError('unknown case')
        return jobs.root/id/'cases'/case_id

    @app.get('/api/v1/experiments/{id}/cases/{case_id}')
    def experiment_case(id:str,case_id:str,view:str='full'):
        from .contracts import load_json
        path=case_path(id,case_id)/'result.json'
        if not path.exists(): raise Conflict('case not yet published')
        if view not in ('full','summary'): raise InputError('unknown case view')
        summary=path.with_name('summary.json')
        if view=='summary' and summary.exists(): return load_json(summary)
        result=load_json(path)
        if view=='summary':
            from .experiments import case_summary
            return case_summary(result)
        if result.get('schema_version')==4:
            from .learn_evaluate import hydrate_run
            return hydrate_run(jobs.root/id,result)
        return result

    @app.get('/api/v1/experiments/{id}/cases/{case_id}/events')
    def experiment_case_events(id:str,case_id:str):
        import json
        path=case_path(id,case_id)/'events.jsonl'
        if not path.exists(): return []
        # A worker may be appending its next line. Expose only complete records.
        return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith('\n')]

    if Path(client_dir).is_dir():
        app.mount('/',StaticFiles(directory=client_dir,html=True),name='client')
    else:
        @app.get('/')
        def no_client(): return dict(message='Build the local client: npm --prefix client run build')
    return app
