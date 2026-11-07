"""Hash-verified SSH bundles. Incomplete transfers never become imported jobs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid

from .contracts import InputError, load_json
from .storage import write_json_atomic

BASE='myrepo/personal/vrp-ai-bench/runs/m4-remote'
IMAGE='docker.io/rocm/pytorch@sha256:4960b7f8484c356246852455a1e4d7933933335f19cd408fd6763eee7e4401bc'


def ssh(host,argv):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',host): raise InputError('invalid SSH alias')
    return subprocess.check_output(['ssh','-o','BatchMode=yes',host,shlex.join(argv)],text=True).strip()


def bundle(source,destination):
    source=Path(source).resolve(); destination=Path(destination)
    files={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.rglob('*')) if p.is_file() and not p.is_symlink()}
    if 'bundle.json' in files: raise InputError('source already contains bundle inventory')
    write_json_atomic(source/'bundle.json',dict(schema_version=1,files=files))
    try:
        with tarfile.open(destination,'w:gz') as archive:
            for name in [*files,'bundle.json']: archive.add(source/name,arcname=name,recursive=False)
    finally: (source/'bundle.json').unlink()
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def unpack(archive,directory):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    if any(directory.iterdir()): raise InputError('bundle extraction requires an empty staging directory')
    with tarfile.open(archive,'r:gz') as stream:
        members=stream.getmembers(); names=set()
        for member in members:
            path=Path(member.name)
            if not member.isfile() or path.is_absolute() or '..' in path.parts or member.name in names:
                raise InputError('unsafe or duplicate bundle member')
            names.add(member.name)
        inventory=json.load(stream.extractfile('bundle.json'))
        if inventory.get('schema_version') != 1 or names != set(inventory['files'])|{'bundle.json'}: raise InputError('bundle inventory mismatch')
        for member in members:
            if member.name=='bundle.json': continue
            data=stream.extractfile(member).read()
            if hashlib.sha256(data).hexdigest()!=inventory['files'][member.name]: raise InputError('corrupt bundle member')
        # Verify all members before writing any publication candidate.
        stream.extractall(directory,filter='data')
    return directory


def source_package(dataset_path,directory):
    from .experiments import provenance
    source=Path(directory); source.mkdir(parents=True,exist_ok=True)
    tracked=subprocess.check_output(['git','ls-files'],text=True).splitlines()
    additional=[str(p) for base in ('engine','schemas','scripts','docs','tests','environments') for p in Path(base).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    for name in sorted(set(tracked+additional+[str(p) for p in Path('.').glob('requirements*.lock')])):
        p=Path(name)
        if p.is_file() and not p.is_symlink():
            target=source/p; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(p,target)
    shutil.copytree(Path(dataset_path).parent,source/'dataset',ignore=shutil.ignore_patterns('*.log'))
    write_json_atomic(source/'source-provenance.json',provenance())


def submit(host,dataset_path,image=IMAGE,output=None):
    if not re.fullmatch(r'[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}',image): raise InputError('remote image must be pinned by digest')
    receipt_path=Path(dataset_path).parent/'preparation.json'
    if not receipt_path.exists(): raise InputError('dataset preparation timing receipt is required')
    receipt=load_json(receipt_path)
    if receipt.get('dataset_hash') != load_json(dataset_path)['dataset_hash']: raise InputError('preparation receipt dataset mismatch')
    if time.time()-receipt['started_unix_s']>=600: raise InputError('preparation/transfer phase exceeded ten minutes before submit')
    identifier=str(uuid.uuid4()); path=f'{BASE}/{identifier}'
    with tempfile.TemporaryDirectory(prefix='vrp-m4-') as temp:
        source=Path(temp)/'source'; source_package(dataset_path,source)
        archive=Path(temp)/'submission.tar.gz'; digest=bundle(source,archive)
        ssh(host,['mkdir','-p',path])
        subprocess.run(['scp','-q',str(archive),f'{host}:{path}/submission.tar.gz.part'],check=True)
        # The remote checkout remains untouched; the worker uses an isolated application bundle.
        code="""import hashlib,sys,tarfile,pathlib
p=pathlib.Path(sys.argv[1]); expected=sys.argv[2]
assert hashlib.sha256(p.read_bytes()).hexdigest()==expected,'transfer hash mismatch'
with tarfile.open(p) as t:
 for m in t.getmembers():
  assert m.isfile() and not pathlib.Path(m.name).is_absolute() and '..' not in pathlib.Path(m.name).parts
 t.extractall(p.parent/'app',filter='data')
p.rename(p.with_suffix(''))
"""
        ssh(host,['python3','-c',code,f'{path}/submission.tar.gz.part',digest])
        remote_home=ssh(host,['sh','-c','printf %s "$HOME"'])
        app=f'{remote_home}/{path}/app'
        command=['podman','run','-d','--name',f'vrp-m4-{identifier}','--network=none','--security-opt','label=disable',
                 '--device','/dev/kfd','--device','/dev/dri','--group-add','keep-groups',
                 '-v',f'{app}:/app','-w','/app','-e','OMP_NUM_THREADS=1','-e','OPENBLAS_NUM_THREADS=1',
                 '-e','MKL_NUM_THREADS=1','-e',f'VRP_CONTAINER_IMAGE={image}',image,
                 'python','-m','engine','learn','campaign','/app/dataset/dataset.json','--root','/app/runs']
        if time.time()-receipt['started_unix_s']>=600:
            raise InputError('preparation/transfer phase exceeded ten minutes; verified bundle retained, worker not started')
        container=ssh(host,command)
    value=dict(id=identifier,host=host,path=path,container=container,image=image,submission_sha256=digest)
    if output: write_json_atomic(output,value)
    return value


def status(ticket):
    value=ssh(ticket['host'],['cat',f"{ticket['path']}/app/runs/campaign-status.json"])
    return json.loads(value)


def cancel(ticket):
    code="""import json,pathlib,sys
root=pathlib.Path(sys.argv[1]); (root/'campaign.cancel').touch()
status=json.loads((root/'campaign-status.json').read_text())
# Only the active queue job is cancelled; completed artifacts remain intact.
import sqlite3
p=root/'jobs/jobs.sqlite'
if p.exists():
 with sqlite3.connect(p) as db:
  db.execute(\"UPDATE jobs SET cancel=1 WHERE status IN ('running','queued')\")
"""
    ssh(ticket['host'],['python3','-c',code,f"{ticket['path']}/app/runs"])
    return status(ticket)


def fetch(ticket,root):
    root=Path(root); staging=root/'.staging'/ticket['id']/str(uuid.uuid4()); staging.mkdir(parents=True,exist_ok=True)
    state=status(ticket)
    if state['status']=='running': raise InputError('fetch requires a terminal campaign; running artifacts stay remote')
    remote=f"{ticket['path']}/app"
    # Use the bundled source's bundle function with host Python (standard library only).
    code="""import sys
sys.path.insert(0,sys.argv[1])
from engine.remote import bundle
print(bundle(sys.argv[1]+'/runs',sys.argv[2]))
"""
    remote_archive=f"{ticket['path']}/results.tar.gz"
    digest=ssh(ticket['host'],['python3','-c',code,remote,remote_archive])
    archive=staging/'results.tar.gz.part'
    subprocess.run(['scp','-q',f"{ticket['host']}:{remote_archive}",str(archive)],check=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=digest: raise InputError('result bundle transfer hash mismatch')
    unpack(archive,staging/'verified')
    directory=staging/'verified'; imported=[]
    from .jobs import Jobs
    from .learn_evaluate import audit
    jobs=Jobs(root)
    for manifest_path in (directory/'jobs').glob('*/manifest.json'):
        campaign=manifest_path.parent; manifest=load_json(manifest_path)
        audited=audit(campaign)
        identifier=campaign.name; destination=jobs.root/identifier
        if destination.exists():
            if load_json(destination/'manifest.json')['manifest_hash']!=manifest['manifest_hash']: raise InputError('conflicting imported campaign')
            for path in campaign.rglob('*'):
                if not path.is_file() or path.name in ('audit.json','report.json','report.csv','report.md'): continue
                existing=destination/path.relative_to(campaign)
                if not existing.is_file() or hashlib.sha256(existing.read_bytes()).digest()!=hashlib.sha256(path.read_bytes()).digest():
                    raise InputError('existing imported campaign differs from verified bundle')
        else:
            # Staging and publication share a filesystem. No partial directory is visible.
            os.rename(campaign,destination)
            descriptor=os.open(jobs.root,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        with jobs.connection() as db:
            import time
            db.execute('INSERT OR IGNORE INTO jobs(id,kind,status,request,created,updated,result,termination_reason) VALUES(?,?,?,?,?,?,?,?)',
                       (identifier,'experiment','completed' if audited['campaign_complete'] else 'failed',json.dumps(dict(manifest=manifest)),time.time(),time.time(),f'{identifier}/report.json','remote_import'))
        imported.append(identifier)
    return dict(status='imported',campaigns=imported,artifacts=str(directory),sha256=digest)


def execute(args):
    if args.action=='doctor':
        return dict(host=args.host,image=IMAGE,inspection=ssh(args.host,['sh','-c',
            'uname -a; cat /etc/os-release; lscpu; free -b; ls -l /dev/kfd /dev/dri; podman version; podman image inspect '+shlex.quote(IMAGE)+' --format "{{json .RepoDigests}}"']))
    if args.action=='submit':
        if not args.input: raise InputError('submit requires frozen dataset.json')
        return submit(args.host,args.input,args.image or IMAGE,args.output)
    if not args.input: raise InputError('remote command requires submission ticket')
    ticket=load_json(args.input)
    if not re.fullmatch(r'[a-f0-9-]{36}',ticket['id']) or ticket['path']!=f"{BASE}/{ticket['id']}": raise InputError('invalid remote ticket')
    return {'status':status,'cancel':cancel,'fetch':lambda t:fetch(t,args.root)}[args.action](ticket)
