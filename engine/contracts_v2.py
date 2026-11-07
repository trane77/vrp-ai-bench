"""Road-backed v2 contracts; v1 remains readable without migration."""
import copy
import json
import math
from pathlib import Path
from .contracts import (SCENARIO, RUN, REPORT, SOLUTION, InputError, obj, array,
                        TEXT, NUM, INT, BOOL, enum, check)

COORD = array({'type': 'number'}, minItems=2, maxItems=2)
LOCATION = obj(id=TEXT, requested=COORD, snapped=COORD, snap_distance_m=NUM)
DATASET = obj(city_id=enum('bologna', 'modena'), version=TEXT, manifest_hash=TEXT,
              source_sha256=TEXT, source_url=TEXT, osm_timestamp=TEXT,
              osrm_image=TEXT, profile_sha256=TEXT, license=enum('ODbL-1.0'),
              attribution=TEXT, path_policy=enum('osrm_car_minimum_profile_weight'))
SCENARIO_V2 = copy.deepcopy(SCENARIO)
SCENARIO_V2['properties'].update(
    schema_version={'type': 'integer', 'const': 2},
    provenance=obj(kind=enum('osm_roads_synthetic_appointments'), description=TEXT,
                   dataset_id=TEXT, dataset_version=TEXT,
                   path_policy=enum('fixed_directed_paths')),
    dataset=DATASET, locations=array(LOCATION),
    generation=obj(seed={'type': 'integer'}, mode=enum('witness_demo', 'edited_demo'),
                   rejected_samples=INT, witness_routes=array(array(TEXT))),
    preparation_s=NUM)
SCENARIO_V2['required'] = list(SCENARIO_V2['properties'])
SCENARIO_V2['properties']['stops']['maxItems'] = 100
leg = SCENARIO_V2['properties']['legs']['items']
leg['properties'].update(geometry=array(COORD), raw_duration_s=NUM,
                         annotations=obj(distance_m=array(NUM), duration_s=array(NUM)),
                         dataset_hash=TEXT)
leg['required'] = list(leg['properties'])
RUN_V2 = copy.deepcopy(RUN)
RUN_V2['properties'].update(schema_version={'type':'integer','const':2}, scenario=SCENARIO_V2,
    job_status=enum('completed','cancelled'),
    termination_reason=enum('budget_exhausted','evaluation_limit','search_exhausted','cancelled'))
RUN_V2['properties']['timing']['properties'].update(recording_s=NUM)
RUN_V2['properties']['timing']['required'].append('recording_s')
EVENT = obj(schema_version={'type':'integer','const':1}, job_id=TEXT, seq=INT,
            elapsed_s=NUM, kind=enum('initial','accepted_insertion','accepted_move',
                'feasible_best','metric','prepared','terminal','recovered'), payload={'type':'object'})


def coordinate(value):
    check(value, COORD)
    if not all(math.isfinite(x) for x in value) or not (-180 <= value[0] <= 180 and -90 <= value[1] <= 90):
        raise InputError('coordinate requires finite longitude/latitude')
    return value


def validate_road_scenario(data):
    from .contracts import validate_scenario
    check(data, SCENARIO_V2)
    # Reuse all v1 domain invariants without weakening the v1 interchange contract.
    base = {key: copy.deepcopy(data[key]) for key in SCENARIO['properties']}
    base['schema_version'] = 1
    base['provenance']['kind'] = 'synthetic'
    base['legs'] = [{k:v for k,v in leg.items() if k in SCENARIO['properties']['legs']['items']['properties']}
                    for leg in base['legs']]
    validate_scenario(base)
    dataset = data['dataset']
    if (data['provenance']['dataset_id'] != dataset['city_id'] or
            data['provenance']['dataset_version'] != dataset['version']):
        raise InputError('mixed city/dataset identity')
    expected = {data['depot_id'], *(s['id'] for s in data['stops'])}
    locations = {p['id']:p for p in data['locations']}
    if set(locations) != expected or len(locations) != len(data['locations']):
        raise InputError('locations must match unique depot/stop IDs')
    for point in locations.values():
        coordinate(point['requested']); coordinate(point['snapped'])
        if point['snap_distance_m'] > 100:
            raise InputError('snapping exceeds 100 meters')
    for leg in data['legs']:
        if leg['dataset_hash'] != dataset['manifest_hash']:
            raise InputError('mixed road dataset hashes')
        annotations = leg['annotations']
        if len(annotations['distance_m']) != len(annotations['duration_s']):
            raise InputError('inconsistent road annotations')
        for point in leg['geometry']:
            coordinate(point)
        if leg['reachable']:
            if len(leg['geometry']) < 2 or leg['travel_s'] != math.ceil(leg['raw_duration_s']):
                raise InputError('reachable leg requires geometry and ceil(raw_duration_s)')
            for key, endpoint in [('source',leg['geometry'][0]),('target',leg['geometry'][-1])]:
                snapped = locations[leg[key]]['snapped']
                if max(abs(a-b) for a,b in zip(endpoint,snapped)) > .0001:
                    raise InputError('road geometry endpoint differs from snapped location')
            if not math.isclose(sum(annotations['distance_m']), sum(s['distance_m'] for s in leg['segments']), rel_tol=1e-6, abs_tol=.1):
                raise InputError('road segment distances differ from annotations')
        elif leg['geometry'] or leg['raw_duration_s'] or any(annotations.values()):
            raise InputError('unreachable road leg must have empty geometry/annotations')
    return copy.deepcopy(data)


def run_schema(data):
    if data.get('schema_version') == 4:
        from .contracts_v4 import RUN_V4
        return RUN_V4
    if data.get('schema_version') == 3:
        from .contracts_v3 import RUN_V3
        return RUN_V3
    return RUN_V2 if data.get('schema_version') == 2 else RUN


if __name__ == '__main__':
    for name, schema in [('scenario-v2',SCENARIO_V2),('run-v2',RUN_V2),('event-v1',EVENT)]:
        Path(f'schemas/{name}.json').write_text(json.dumps({'$schema':'https://json-schema.org/draft/2020-12/schema', **schema},indent=2)+'\n')
