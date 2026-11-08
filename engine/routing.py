"""OSRM adapter. Provider failure is never an unreachable-road sentinel."""
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from .contracts import InputError
from .contracts_v2 import coordinate


class ProviderError(RuntimeError): pass
class Cancelled(RuntimeError): pass


class OSRM:
    def __init__(self,url='http://127.0.0.1:5000',cancelled=lambda:False):
        parsed=urllib.parse.urlparse(url)
        if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1'):
            raise InputError('OSRM must be a configured loopback HTTP endpoint')
        self.url=url.rstrip('/'); self.cancelled=cancelled

    def request(self,path):
        if self.cancelled(): raise Cancelled('cancelled')
        try:
            with urllib.request.urlopen(self.url+path,timeout=10) as response:
                data=json.load(response)
        except urllib.error.HTTPError as exc:
            try: data=json.load(exc)
            except (ValueError,OSError): raise ProviderError('OSRM HTTP error') from exc
            if data.get('code') not in ('NoRoute','NoSegment'):
                raise ProviderError(f'OSRM HTTP error {exc.code}') from exc
        except (OSError,ValueError,TimeoutError) as exc:
            raise ProviderError('OSRM unavailable or malformed response') from exc
        if self.cancelled(): raise Cancelled('cancelled')
        if not isinstance(data,dict) or data.get('code') not in ('Ok','NoRoute','NoSegment'):
            raise ProviderError('OSRM returned an unexpected status')
        return data

    def snap(self,point):
        coordinate(point)
        data=self.request(f'/nearest/v1/driving/{point[0]},{point[1]}?number=1&radiuses=100')
        try:
            if data['code']!='Ok': raise InputError('no road within 100 meters')
            waypoint=data['waypoints'][0]
            coordinate(waypoint['location'])
            distance=waypoint['distance']
            if not isinstance(distance,(int,float)) or not math.isfinite(distance) or not 0<=distance<=100:
                raise InputError('snapping exceeds 100 meters')
            return dict(requested=point,snapped=waypoint['location'],snap_distance_m=distance)
        except (KeyError,IndexError,TypeError) as exc:
            raise ProviderError('malformed OSRM nearest response') from exc

    def route(self,source,target,dataset_hash):
        a,b=source['snapped'],target['snapped']
        data=self.request(f'/route/v1/driving/{a[0]},{a[1]};{b[0]},{b[1]}?alternatives=3&overview=full&geometries=geojson&annotations=distance,duration')
        result=dict(source=source['id'],target=target['id'],dataset_hash=dataset_hash)
        if data['code']=='NoRoute':
            return dict(**result,reachable=False,travel_s=0,segments=[],geometry=[],raw_duration_s=0,
                        annotations=dict(distance_m=[],duration_s=[]))
        if data['code']!='Ok': raise ProviderError('OSRM could not snap frozen route endpoints')
        try:
            def parse_one(route_obj):
                annotation=route_obj['legs'][0]['annotation']
                durations=annotation['duration']; distances=annotation['distance']
                raw=route_obj['duration']; geometry=route_obj['geometry']['coordinates']
                if (not isinstance(raw,(int,float)) or not math.isfinite(raw) or raw<0 or
                    len(durations)!=len(distances) or len(geometry)<2):
                    raise ValueError('invalid route values')
                segments=[]
                for d,t in zip(distances,durations):
                    if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>=0 for v in (d,t)):
                        raise ValueError('invalid annotations')
                    speed=d/t*3.6 if t else 0
                    category='slow' if speed<=30 else 'urban' if speed<=60 else 'fast'
                    segments.append(dict(distance_m=d,driving_category=category))
                if not segments: segments=[dict(distance_m=0,driving_category='slow')]
                for point in geometry: coordinate(point)
                return dict(travel_s=math.ceil(raw),distance_m=sum(distances),segments=segments,
                            geometry=geometry,raw_duration_s=raw,
                            annotations=dict(distance_m=distances,duration_s=durations))

            primary=parse_one(data['routes'][0])
            leg_dict=dict(**result,reachable=True,travel_s=primary['travel_s'],segments=primary['segments'],
                          geometry=primary['geometry'],raw_duration_s=primary['raw_duration_s'],
                          annotations=primary['annotations'])
            if len(data['routes'])>1:
                alts=[]
                seen_keys=set()
                for r_obj in data['routes']:
                    parsed=parse_one(r_obj)
                    key=(parsed['travel_s'],round(parsed['distance_m'],1))
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    alts.append(dict(id=f'alt-{len(alts)}',travel_s=parsed['travel_s'],
                                     distance_m=parsed['distance_m'],segments=parsed['segments'],
                                     geometry=parsed['geometry'],raw_duration_s=parsed['raw_duration_s']))
                if len(alts)>1:
                    leg_dict['alternatives']=alts
            return leg_dict
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise ProviderError('malformed OSRM route response') from exc
