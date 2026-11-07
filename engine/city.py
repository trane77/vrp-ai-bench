"""Explicit offline road preparation and immutable installed-city manifests."""
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import urllib.request
import zlib
from datetime import datetime, timezone

from .contracts import InputError, load_json
from .storage import write_json_atomic

OSRM_IMAGE = 'ghcr.io/project-osrm/osrm-backend@sha256:729461bcc9ae9e6aafa92c0f93db9b060a32e85d5e72092c01ae4a4a9f1eb564'
SOURCE_URL = 'https://download.geofabrik.de/europe/italy/nord-est-latest.osm.pbf'
PRESETS = {'bologna': {'name':'Bologna', 'center':[11.3426,44.4949]},
           'modena': {'name':'Modena', 'center':[10.9252,44.6471]}}
ATTRIBUTION = '© OpenStreetMap contributors — https://www.openstreetmap.org/copyright; extract by Geofabrik'


def digest_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def manifest_hash(manifest):
    return hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def fields(raw):
    """Read the limited protobuf wire types needed by an OSM PBF header."""
    def varint(offset):
        value=0
        for shift in range(0,70,7):
            byte=raw[offset]; offset+=1
            value |= (byte & 127) << shift
            if byte < 128: return value,offset
        raise InputError('invalid PBF varint')
    offset=0
    while offset<len(raw):
        tag,offset=varint(offset); number,wire=tag>>3,tag&7
        if wire==0: value,offset=varint(offset)
        elif wire==2:
            length,offset=varint(offset)
            value=raw[offset:offset+length]; offset+=length
        elif wire in (1,5):
            size=8 if wire==1 else 4; value=raw[offset:offset+size]; offset+=size
        else: raise InputError('unsupported PBF header wire type')
        yield number,value


def pbf_timestamp(path):
    with Path(path).open('rb') as stream:
        size=struct.unpack('>I',stream.read(4))[0]
        if size>65536: raise InputError('invalid PBF header size')
        header=dict(fields(stream.read(size)))
        if header.get(1)!=b'OSMHeader': raise InputError('OSM PBF header missing')
        blob=dict(fields(stream.read(header[3])))
        raw=blob.get(1) or zlib.decompress(blob[3])
        stamp=dict(fields(raw)).get(32)
        if not isinstance(stamp,int): raise InputError('OSM snapshot timestamp missing')
        return datetime.fromtimestamp(stamp,timezone.utc).isoformat()


class CityRegistry:
    def __init__(self, root='data'):
        self.root=Path(root).resolve()

    def list(self):
        result=[]
        for city_id,preset in PRESETS.items():
            path=self.root/'cities'/city_id/'manifest.json'
            result.append(dict(id=city_id,**preset,installed=path.exists(),
                               manifest=load_json(path) if path.exists() else None))
        return result

    def get(self,city_id):
        if city_id not in PRESETS: raise InputError('unknown city')
        path=self.root/'cities'/city_id/'manifest.json'
        if not path.exists(): raise InputError('city not installed; run city prepare')
        manifest=load_json(path)
        recorded=manifest.pop('manifest_hash',None)
        if manifest_hash(manifest)!=recorded or manifest.get('city_id')!=city_id:
            raise InputError('city manifest identity/hash mismatch')
        manifest['manifest_hash']=recorded
        return manifest

    def verify(self,city_id):
        manifest=self.get(city_id)
        for relative,expected in manifest['files'].items():
            path=(self.root/relative).resolve()
            if not path.is_relative_to(self.root) or digest_file(path)!=expected:
                raise InputError(f'city artifact checksum mismatch: {relative}')
        return manifest


def docker(*arguments, capture=False):
    try:
        result=subprocess.run(['docker',*arguments],text=True,
                              stdout=subprocess.PIPE if capture else None,
                              stderr=subprocess.PIPE,check=True)
    except subprocess.CalledProcessError as exc:
        detail=(exc.stderr or exc.stdout or 'no diagnostic output').strip()
        raise RuntimeError(f'Docker {arguments[0]} failed (exit {exc.returncode}): {detail}') from exc
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.stdout.strip() if capture else None


def prepare(root='data', source=None):
    """One explicit preparation builds a shared graph and both city manifests."""
    registry=CityRegistry(root); directory=registry.root/'source'
    directory.mkdir(parents=True,exist_ok=True)
    docker('version',capture=True)  # No system configuration changes.
    if docker('ps','--filter','name=^/vrp-ai-osrm$','--format','{{.ID}}',capture=True):
        raise InputError('stop the owned OSRM container before rebuilding road data')
    path=directory/'nord-est.osm.pbf'
    if source:
        import shutil
        source=Path(source).resolve()
        if source!=path: shutil.copyfile(source,path)
    elif not path.exists():
        temporary=path.with_suffix('.download')
        with urllib.request.urlopen(SOURCE_URL,timeout=60) as response, temporary.open('wb') as stream:
            import shutil
            shutil.copyfileobj(response,stream)
        temporary.replace(path)
    source_hash=digest_file(path)
    stamp=pbf_timestamp(path)
    docker('pull',OSRM_IMAGE)
    profile=docker('run','--rm',OSRM_IMAGE,'cat','/opt/car.lua',capture=True)+'\n'
    (directory/'car.lua').write_text(profile)
    for executable,extra in [('osrm-extract',['-p','/opt/car.lua']),('osrm-partition',[]),('osrm-customize',[])]:
        filename='/data/nord-est.osm.pbf' if executable=='osrm-extract' else '/data/nord-est.osrm'
        docker('run','--rm','--user',f'{os.getuid()}:{os.getgid()}','--memory=10g','-v',f'{directory}:/data',OSRM_IMAGE,
               executable,'-t','2',*extra,filename)
    return publish_prepared(registry.root)


def publish_prepared(root='data'):
    """Publish manifests after the three OSRM stages have completed successfully."""
    registry=CityRegistry(root); directory=registry.root/'source'
    path=directory/'nord-est.osm.pbf'
    source_hash=digest_file(path); stamp=pbf_timestamp(path)
    for suffix in ('properties','partition','cells','cell_metrics'):
        if not (directory/f'nord-est.osrm.{suffix}').is_file():
            raise InputError(f'OSRM artifact missing: {suffix}')
    files={str(p.relative_to(registry.root)):digest_file(p) for p in directory.glob('nord-est.osrm*') if p.is_file()}
    files.update({'source/nord-est.osm.pbf':source_hash,'source/car.lua':digest_file(directory/'car.lua')})
    if len(files)<3: raise InputError('OSRM preparation produced no graph')
    for city_id,preset in PRESETS.items():
        manifest=dict(schema_version=1,city_id=city_id,**preset,radius_m=5000,
            version=source_hash[:16],source_url=SOURCE_URL,source_sha256=source_hash,
            osm_timestamp=stamp,osrm_image=OSRM_IMAGE,profile_sha256=files['source/car.lua'],
            license='ODbL-1.0',attribution=ATTRIBUTION,
            path_policy='osrm_car_minimum_profile_weight',files=files,
            limitations=['Synthetic appointments on real OSM roads','Uncalibrated fuel estimates',
                         'Car profile does not certify van access, ZTL permits or operational legality'])
        manifest['manifest_hash']=manifest_hash(manifest)
        write_json_atomic(registry.root/'cities'/city_id/'manifest.json',manifest,overwrite=True)
    return registry.list()


def serve(root='data'):
    registry=CityRegistry(root)
    manifest=registry.verify('bologna'); registry.verify('modena')
    existing=docker('ps','-a','--filter','name=^/vrp-ai-osrm$','--format','{{.ID}}',capture=True)
    if existing:
        container=json.loads(docker('inspect','vrp-ai-osrm',capture=True))[0]
        if not container['State']['Running']:
            raise InputError('existing vrp-ai-osrm container is not running; inspect it with '
                             'docker inspect vrp-ai-osrm and docker logs vrp-ai-osrm before restarting it')
        verify_service(registry,manifest,container=container)
        return container['Id']
    return docker('run','--rm','-d','--name','vrp-ai-osrm','--memory=6g',
                  '--label',f'org.vrp-ai.source_sha256={manifest["source_sha256"]}',
                  '-p','127.0.0.1:5000:5000','-v',f'{registry.root / "source"}:/data:ro',
                  OSRM_IMAGE,'osrm-routed','--algorithm','mld','-t','2','/data/nord-est.osrm',capture=True)


def verify_service(registry,manifest,*,container=None):
    """OSRM's API omits dataset identity, so verify the owned local container."""
    if container is None:
        container=json.loads(docker('inspect','vrp-ai-osrm',capture=True))[0]
    expected=str((registry.root/'source').resolve())
    if (not container['State']['Running'] or container['Config']['Image']!=OSRM_IMAGE
        or container['Config'].get('Labels',{}).get('org.vrp-ai.source_sha256')!=manifest['source_sha256']
        or not any(m['Destination']=='/data' and m['Source']==expected and not m['RW'] for m in container['Mounts'])):
        raise InputError('running OSRM does not match the installed snapshot; restart with city serve')
    ports=container['NetworkSettings']['Ports'].get('5000/tcp') or []
    if not any(p['HostIp']=='127.0.0.1' and p['HostPort']=='5000' for p in ports):
        raise InputError('OSRM must expose only the expected loopback port')


def distance_m(a,b):
    lon1,lat1,lon2,lat2=map(math.radians,[*a,*b])
    value=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000*2*math.asin(min(1,math.sqrt(value)))
