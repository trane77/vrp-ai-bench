"""Durable queue and event journal. HTTP connections never own computation."""
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .contracts import InputError, check, load_json
from .contracts_v2 import EVENT
from .solver import scenario_hash
from .storage import write_json_atomic

TERMINAL={'completed','cancelled','failed'}


class Conflict(InputError): pass


class Jobs:
    def __init__(self,root='runs'):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, revision TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    request TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, cancel INTEGER DEFAULT 0,
                    result TEXT, error TEXT, termination_reason TEXT, feasibility TEXT DEFAULT 'unknown');
                CREATE TABLE IF NOT EXISTS events(job_id TEXT NOT NULL,seq INTEGER NOT NULL,data TEXT NOT NULL,
                    PRIMARY KEY(job_id,seq));
                CREATE TABLE IF NOT EXISTS snapshots(job_id TEXT NOT NULL,seq INTEGER NOT NULL,data TEXT NOT NULL,
                    PRIMARY KEY(job_id,seq));
            ''')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.root/'jobs.sqlite',timeout=20)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()

    def draft(self,id):
        with self.connection() as db:
            row=db.execute('SELECT * FROM drafts WHERE id=?',(id,)).fetchone()
        if row is None: raise KeyError('draft not found')
        return dict(id=row['id'],revision=row['revision'],data=json.loads(row['data']))

    def save_draft(self,data,id=None,expected=None):
        id=id or str(uuid.uuid4()); revision=scenario_hash(data)
        with self.connection() as db:
            if expected is not None:
                updated=db.execute('UPDATE drafts SET revision=?,data=? WHERE id=? AND revision=?',
                    (revision,json.dumps(data),id,expected)).rowcount
                if not updated: raise Conflict('draft revision changed; reload before editing')
            else:
                db.execute('INSERT INTO drafts VALUES(?,?,?)',(id,revision,json.dumps(data)))
        return self.draft(id)

    def submit(self,kind,request):
        if kind not in ('prepare','solve','experiment','collect','train'): raise InputError('unsupported job kind')
        if kind == 'experiment':
            from .experiments import validate_manifest
            validate_manifest(request['manifest'])
        id=str(uuid.uuid4()); now=time.time()
        with self.connection() as db:
            draft=db.execute('SELECT revision FROM drafts WHERE id=?',(request['draft_id'],)).fetchone() if kind in ('prepare','solve') else None
            if kind in ('prepare','solve') and (draft is None or draft['revision']!=request['revision']):
                raise Conflict('draft revision is stale')
            db.execute('INSERT INTO jobs(id,kind,status,request,created,updated) VALUES(?,?,?,?,?,?)',
                       (id,kind,'queued',json.dumps(request),now,now))
        return self.get(id)

    def get(self,id):
        with self.connection() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
            if row is None: raise KeyError('job not found')
            value=dict(row)
            value['last_seq']=db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE job_id=?',(id,)).fetchone()[0]
        value['request']=json.loads(value['request']); value['cancel_requested']=bool(value.pop('cancel'))
        return value

    def list(self):
        with self.connection() as db:
            ids=[row[0] for row in db.execute('SELECT id FROM jobs ORDER BY created DESC LIMIT 200')]
        return [self.get(id) for id in ids]

    def claim(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row is None: return None
            db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(time.time(),row[0]))
        return self.get(row[0])

    def cancel(self,id):
        self.get(id)
        now=time.time()
        with self.connection() as db:
            # Queued jobs have no worker: transition immediately to cancelled so the client
            # does not wait indefinitely for a worker that may not be running.
            db.execute(
                "UPDATE jobs SET status='cancelled',termination_reason='cancelled',"
                "cancel=1,updated=? WHERE id=? AND status='queued'",
                (now,id))
            # Running jobs: set the flag; the worker will honour it on the next check.
            db.execute(
                "UPDATE jobs SET cancel=1,updated=? WHERE id=? AND status='running'",
                (now,id))
        return self.get(id)

    def result(self,id):
        job=self.get(id)
        if job['status'] not in TERMINAL or not job['result']: raise Conflict('result not available')
        return load_json(self.root/job['result'])

    def finish(self,id,status,reason,feasibility='unknown',result=None,error=None):
        with self.connection() as db:
            db.execute('UPDATE jobs SET status=?,termination_reason=?,feasibility=?,result=?,error=?,updated=? WHERE id=?',
                (status,reason,feasibility,result,error,time.time(),id))

    def events(self,id,after=0,limit=500):
        self.get(id)
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT data FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?', (id,after,limit))]

    def snapshot(self,id,before=None):
        self.get(id)
        with self.connection() as db:
            row=db.execute('SELECT data FROM snapshots WHERE job_id=? AND seq<=? ORDER BY seq DESC LIMIT 1',
                           (id,before if before is not None else 9223372036854775807)).fetchone()
        return json.loads(row[0]) if row else None

    def emit(self,id,kind,payload,elapsed_s):
        # Only the job's worker writes events. Queue cancellation is a flag, not another writer.
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            seq=db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE job_id=?',(id,)).fetchone()[0]
            event=dict(schema_version=1,job_id=id,seq=seq,elapsed_s=max(0,elapsed_s),kind=kind,payload=payload)
            check(event,EVENT)
            encoded=json.dumps(event,allow_nan=False,separators=(',',':'))
            directory=self.root/id; directory.mkdir(exist_ok=True)
            segment=directory/f'events-{(seq-1)//1000:06}.jsonl'
            total=sum(p.stat().st_size for p in directory.glob('events-*'))
            if total+len(encoded)>1024**3: raise OSError('event trace exceeded 1 GiB')
            # File is fsynced before the SQLite commit makes an event visible.
            # Unindexed trailing bytes after a crash are not replayed.
            with segment.open('a',encoding='utf-8') as stream:
                stream.write(encoded+'\n'); stream.flush(); os.fsync(stream.fileno())
            previous=db.execute('SELECT data FROM snapshots WHERE job_id=? ORDER BY seq DESC LIMIT 1',(id,)).fetchone()
            # State folding reads intervening indexed events, not uncommitted file tails.
            state=json.loads(previous[0]) if previous else dict(job_id=id,seq=0,current=None,best=None,terminal=None)
            tail=db.execute('SELECT data FROM events WHERE job_id=? AND seq>? ORDER BY seq',(id,state['seq']))
            for row in tail: state=fold(state,json.loads(row[0]))
            state=fold(state,event)
            db.execute('INSERT INTO events VALUES(?,?,?)',(id,seq,encoded))
            if seq==1 or seq%100==0 or kind in ('terminal','prepared','recovered'):
                write_json_atomic(directory/f'snapshot-{seq:09}.json',state,overwrite=True)
                db.execute('INSERT INTO snapshots VALUES(?,?,?)',(id,seq,json.dumps(state,allow_nan=False)))
        # Closed segments are immutable; SQLite retains the indexed event stream.
        if seq%1000==0 or kind in ('terminal','recovered'):
            for segment in directory.glob('events-*.jsonl'):
                with segment.open('rb') as source, gzip.open(str(segment)+f'.through-{seq}.gz','wb') as target:
                    import shutil
                    shutil.copyfileobj(source,target)
                segment.unlink()
        return event

    def recover(self):
        # Called only after obtaining the exclusive worker lock.
        for job in self.list():
            if job['status']=='running':
                state=self.snapshot(job['id']) or dict(job_id=job['id'],seq=0,current=None,best=None,terminal=None)
                elapsed=0
                while True:
                    batch=self.events(job['id'],state['seq'])
                    if not batch: break
                    for event in batch:
                        state=fold(state,event); elapsed=event['elapsed_s']
                if not elapsed and state['seq']:
                    elapsed=self.events(job['id'],state['seq']-1,1)[0]['elapsed_s']
                self.emit(job['id'],'recovered',dict(status='failed',reason='interrupted',best=state.get('best')),elapsed)
                self.finish(job['id'],'failed','interrupted',error='Worker interrupted; recorded progress retained')


def fold(state,event):
    state=dict(state); payload=event['payload']; state['seq']=event['seq']
    if event['kind'] in ('accepted_insertion','accepted_move','initial'):
        state['current']=payload
    elif event['kind']=='feasible_best':
        state['best']=payload
    elif event['kind']=='prepared':
        state['prepared']=payload
    elif event['kind'] in ('terminal','recovered'):
        state['terminal']=payload
        if payload.get('best') is not None: state['best']=payload['best']
    return state
