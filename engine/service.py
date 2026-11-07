"""Loopback HTTP/WebSocket facade; computation belongs to engine.worker."""
import asyncio
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


def same_origin(origin,host):
    try:
        parsed=urlparse(origin)
        return parsed.scheme in ('http','https') and parsed.netloc==host
    except ValueError: return False


def create_app(root='runs',data_root='data',client_dir='client/dist'):
    app=FastAPI(title='VRP AI local laboratory',version='0.2.0')
    jobs=Jobs(root); registry=CityRegistry(data_root)
    app.state.jobs=jobs; app.state.registry=registry
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','[::1]'])

    @app.middleware('http')
    async def protect_origin(request:Request,call_next):
        origin=request.headers.get('origin')
        if origin and not same_origin(origin,request.headers.get('host','')):
            return JSONResponse(status_code=403,content={'detail':'cross-origin request rejected'})
        if request.headers.get('content-length','0').isdigit() and int(request.headers.get('content-length','0'))>32*1024*1024:
            return JSONResponse(status_code=413,content={'detail':'request exceeds 32 MiB'})
        return await call_next(request)

    @app.exception_handler(InputError)
    async def invalid(request,exc):
        return JSONResponse(status_code=409 if isinstance(exc,Conflict) else 422,content={'detail':str(exc)})

    @app.exception_handler(KeyError)
    async def missing(request,exc):
        return JSONResponse(status_code=404,content={'detail':str(exc)})

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
        data['mode']='edited_demo'
        return jobs.save_draft(data,id,body.revision)

    @app.post('/api/v1/jobs',status_code=202)
    def new_job(body:NewJob):
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
    def list_jobs(): return jobs.list()

    @app.get('/api/v1/jobs/{id}')
    def get_job(id:str): return jobs.get(id)

    @app.post('/api/v1/jobs/{id}/cancel')
    def cancel(id:str): return jobs.cancel(id)

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
        if after<0 or not same_origin(socket.headers.get('origin',''),socket.headers.get('host','')):
            await socket.close(code=1008); return
        try: job=jobs.get(id)
        except KeyError:
            await socket.close(code=1008); return
        await socket.accept()
        cursor=after
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
                await asyncio.sleep(.2)
        except (WebSocketDisconnect,RuntimeError): pass
        finally:
            try: await socket.close()
            except RuntimeError: pass

    if Path(client_dir).is_dir():
        app.mount('/',StaticFiles(directory=client_dir,html=True),name='client')
    else:
        @app.get('/')
        def no_client(): return dict(message='Build the local client: npm --prefix client run build')
    return app
