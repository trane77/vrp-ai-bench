"""Editable demo inputs and frozen road-backed scenarios."""
import copy
import math
import random
import time
import uuid

from .city import PRESETS, distance_m
from .contracts import SCENARIO, STOP, InputError, obj, array, TEXT, INT, NUM, check, enum, validate_scenario
from .contracts_v2 import COORD, coordinate
from .routing import Cancelled
from .solver import proposal
from .validation import validate_solution

PARAMETER_KEYS = ['start_s','vehicle_ids','capacity','duration_cap_s','service','fuel_model','objective']
OPTIONAL_PARAMETER_KEYS = ['path_optimization','vehicle_capacities','driver_break']
DRAFT_STOP=copy.deepcopy(STOP)
DRAFT_STOP['properties']['coordinate']=COORD
DRAFT_STOP['required'].append('coordinate')
DRAFT_PARAMS=obj(**{key:SCENARIO['properties'][key] for key in PARAMETER_KEYS})
for _k in OPTIONAL_PARAMETER_KEYS:
    DRAFT_PARAMS['properties'][_k]=SCENARIO['properties'][_k]
DRAFT = obj(schema_version={'type':'integer','const':1}, city_id=enum(*PRESETS), dataset_hash=TEXT,
    seed={'type':'integer'}, mode=enum('witness_demo','edited_demo'), depot=COORD,
    stops=array(DRAFT_STOP,maxItems=2000),
    parameters=DRAFT_PARAMS)


def make_draft(manifest,seed=42,count=20,*,vehicles=2,service_mode='constant',duration_cap_s=None):
    if type(seed)!=int or type(count)!=int or not 1<=count<=1500:
        raise InputError('seed must be an integer; stop count must be 1–1500')
    if type(vehicles)!=int or not 1<=vehicles<=50:
        raise InputError('vehicles must be an integer between 1 and 50')
    if service_mode not in ('constant','packages'):
        raise InputError('service_mode must be constant or packages')
    rng=random.Random(seed)
    center=manifest['center']
    stops=[]
    max_radius = 1500 if count <= 100 else (3500 if count <= 400 else 7500)
    for i in range(count):
        angle=rng.random()*2*math.pi; radius=math.sqrt(rng.random())*max_radius
        point=[center[0]+math.cos(angle)*radius/(111320*math.cos(math.radians(center[1]))),
               center[1]+math.sin(angle)*radius/111320]
        packages=rng.randint(1,5)
        stops.append(dict(id=f'stop-{i+1:03}',coordinate=point,window_open_s=0,window_close_s=28800,
                          packages=packages,mass_kg=packages*2,volume_m3=packages*.01))
    fuel=dict(version='linear-load-v1',kind='load_dependent',calibration='uncalibrated',
              label='experimental estimate',categories={key:dict(base_l_per_km=.1,load_l_per_km_kg=.001)
              for key in ['slow','urban','fast']},idle_policy='off',idle_l_per_s=0)
    return dict(schema_version=1,city_id=manifest['city_id'],dataset_hash=manifest['manifest_hash'],
        seed=seed,mode='witness_demo',depot=center,stops=stops,
        parameters=dict(start_s=0,vehicle_ids=[f'van-{i+1}' for i in range(vehicles)],capacity=dict(mass_kg=1000,volume_m3=10),
            duration_cap_s=duration_cap_s,service=dict(mode=service_mode,constant_s=60,fixed_s=30,per_package_s=10),
            fuel_model=fuel,objective=dict(time_weight=.5,fuel_weight=.5,time_scale_s=3600,fuel_scale_l=10)))


def validate_draft(draft,manifest):
    check(draft,DRAFT)
    if draft['city_id']!=manifest['city_id'] or draft['dataset_hash']!=manifest['manifest_hash']:
        raise InputError('draft belongs to another dataset/version')
    ids=set()
    for point in [draft['depot'],*(stop['coordinate'] for stop in draft['stops'])]:
        coordinate(point)
        if distance_m(manifest['center'],point)>manifest['radius_m']:
            raise InputError('depot and stops must lie inside the 5 km service area')
    for stop in draft['stops']:
        if stop['id']=='depot' or stop['id'] in ids or stop['window_open_s']>stop['window_close_s']:
            raise InputError('invalid stop IDs/windows')
        ids.add(stop['id'])
    # Validate parameter semantics without requiring road preparation.
    base=dict(schema_version=1,id='parameters',provenance=dict(kind='synthetic',description='parameter check',
        dataset_id='parameters',dataset_version='1',path_policy='fixed_directed_paths'),
        reference_instant='2026-01-01T00:00:00+00:00',timezone='Europe/Rome',depot_id='depot',stops=[],legs=[],
        **copy.deepcopy(draft['parameters']))
    validate_scenario(base)
    return copy.deepcopy(draft)


def prepare_draft(draft,manifest,provider,progress=lambda *args:None,cancelled=lambda:False):
    started=time.monotonic()
    d=validate_draft(draft,manifest)
    rng=random.Random(d['seed']); rejected=0
    def guard():
        if cancelled(): raise Cancelled('cancelled')
    def snap(id,point,resample=False):
        nonlocal rejected
        for attempt in range(500):
            guard()
            try:
                location=dict(id=id,**provider.snap(point))
                if resample:
                    outward=provider.route(locations[0],location,manifest['manifest_hash'])
                    inward=provider.route(location,locations[0],manifest['manifest_hash'])
                    if not outward['reachable'] or not inward['reachable']:
                        raise InputError('sample disconnected from depot')
                    # OSRM may snap route queries away from a nearest point on a
                    # small disconnected component. Such a sample is not routable
                    # at its recorded location and must enter recorded resampling.
                    for leg in (outward,inward):
                        for endpoint, point in ((leg['geometry'][0], locations[0] if leg['source']=='depot' else location),
                                                (leg['geometry'][-1], locations[0] if leg['target']=='depot' else location)):
                            if max(abs(a-b) for a,b in zip(endpoint,point['snapped'])) > .0001:
                                raise InputError('sample route endpoint differs from nearest snap')
                return location
            except InputError:
                if not resample: raise
                rejected+=1
                angle=rng.random()*math.tau; radius=math.sqrt(rng.random())*1500
                center=manifest['center']
                point=[center[0]+math.cos(angle)*radius/(111320*math.cos(math.radians(center[1]))),
                       center[1]+math.sin(angle)*radius/111320]
        raise InputError('could not construct demo samples within 500 attempts')
    locations=[snap('depot',d['depot'])]
    for stop in d['stops']:
        locations.append(snap(stop['id'],stop['coordinate'],d['mode']=='witness_demo'))
        progress('metric',dict(phase='snapping',done=len(locations)-1,total=len(d['stops'])))
    legs=[]; total=len(locations)*(len(locations)-1)
    for source in locations:
        for target in locations:
            if source['id']==target['id']: continue
            guard()
            legs.append(provider.route(source,target,manifest['manifest_hash']))
            progress('metric',dict(phase='paths',done=len(legs),total=total))
    dataset={key:manifest[key] for key in ['city_id','version','manifest_hash','source_sha256','source_url',
        'osm_timestamp','osrm_image','profile_sha256','license','attribution','path_policy']}
    path_policy='osrm_alternatives_evaluated' if d['parameters'].get('path_optimization') else 'fixed_directed_paths'
    s=dict(schema_version=2,id=str(uuid.uuid4()),provenance=dict(kind='osm_roads_synthetic_appointments',
        description='Synthetic demo appointments on real OSM roads; witness construction bias disclosed',
        dataset_id=d['city_id'],dataset_version=manifest['version'],path_policy=path_policy),
        dataset=dataset,locations=locations,generation=dict(seed=d['seed'],mode=d['mode'],
        rejected_samples=rejected,witness_routes=[]),preparation_s=0,
        reference_instant='2026-01-01T00:00:00+00:00',timezone='Europe/Rome',depot_id='depot',
        stops=[{k:v for k,v in stop.items() if k!='coordinate'} for stop in d['stops']],legs=legs,
        **d['parameters'])
    if d['mode']=='witness_demo' or all(stop['window_open_s']==0 and stop['window_close_s']==28800 for stop in d['stops']):
        routes=[[] for _ in s['vehicle_ids']]
        for i,stop in enumerate(s['stops']): routes[i%len(routes)].append(stop['id'])
        lookup={(leg['source'],leg['target']):leg for leg in legs}
        stops={stop['id']:stop for stop in s['stops']}
        brk=s.get('driver_break')
        brk_enabled=bool(brk and brk.get('enabled'))
        for route in routes:
            now=s['start_s']; previous='depot'; work_s=0
            for id in route:
                leg=lookup[previous,id]
                if not leg['reachable']: raise InputError('demo witness contains an unreachable leg')
                now+=leg['travel_s']; work_s+=leg['travel_s']; stop=stops[id]
                stop['window_open_s']=max(s['start_s'],now-300)
                stop['window_close_s']=now+300
                service=s['service']
                dur=(service['constant_s'] if service['mode']=='constant' else service['fixed_s']+service['per_package_s']*stop['packages'])
                now+=dur; work_s+=dur
                if brk_enabled and work_s>=brk['threshold_s']:
                    now+=int(brk['duration_s']); work_s=0
                previous=id
        s['generation']['witness_routes']=routes
        report=validate_solution(validate_scenario(s),proposal(s,routes))
        if not report['feasible']: raise InputError('demo witness violates configured constraints')
    validated=validate_scenario(s)
    validated['preparation_s']=time.monotonic()-started
    return validated


def draft_from_scenario(scenario):
    locations={point['id']:point for point in scenario['locations']}
    params={key:copy.deepcopy(scenario[key]) for key in PARAMETER_KEYS}
    for key in OPTIONAL_PARAMETER_KEYS:
        if key in scenario:
            params[key]=copy.deepcopy(scenario[key])
    return dict(schema_version=1,city_id=scenario['dataset']['city_id'],
        dataset_hash=scenario['dataset']['manifest_hash'],seed=scenario['generation']['seed'],mode=scenario['generation']['mode'],
        depot=locations['depot']['requested'],stops=[dict(**stop,coordinate=locations[stop['id']]['requested']) for stop in scenario['stops']],
        parameters=params)
