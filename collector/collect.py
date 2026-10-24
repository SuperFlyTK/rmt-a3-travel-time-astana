"""Collect public route estimates; never log credentials or follow redirects."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['timestamp_utc','timestamp_astana','cycle_id','route_id','provider','duration_s',
          'duration_no_traffic_s','distance_m','status','http_code','error','request_id',
          'latency_s','geometry_file','raw_file','phase']
KEYS = {'ors':'ORS_KEY','tomtom':'TOMTOM_KEY','dgis':'DGIS_KEY','google':'GOOGLE_MAPS_KEY','here':'HERE_KEY'}
ASTANA = timezone(timedelta(hours=5))

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None

def load_keys():
    env = dict(os.environ)
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                name,value=line.split('=',1)
                env.setdefault(name.strip(),value.strip().strip('"\''))
    return {provider:env.get(name,'') for provider,name in KEYS.items()}

def scrub(value, secrets):
    if isinstance(value,dict):
        return {k:scrub(v,secrets) for k,v in value.items() if k.lower() not in {'key','api_key','apikey','authorization'}}
    if isinstance(value,list): return [scrub(v,secrets) for v in value]
    if isinstance(value,str):
        for secret in secrets:
            if secret: value=value.replace(secret,'[REDACTED]')
    return value

def build_request(provider, route, key):
    a=(float(route['origin_lat']),float(route['origin_lon']))
    b=(float(route['destination_lat']),float(route['destination_lon']))
    headers={'Accept':'application/json','User-Agent':'RMT-A3-Astana-Educational-Instrument/1.0'}
    body=None
    if provider=='ors':
        url='https://api.heigit.org/openrouteservice/v2/directions/driving-car/geojson'
        headers['Authorization']=key
        body={'coordinates':[[a[1],a[0]],[b[1],b[0]]],'instructions':False,'units':'m'}
    elif provider=='tomtom':
        url=f'https://api.tomtom.com/routing/1/calculateRoute/{a[0]},{a[1]}:{b[0]},{b[1]}/json?'+urlencode({'key':key,'traffic':'true','departAt':'now','travelMode':'car','routeType':'fastest','computeTravelTimeFor':'all','routeRepresentation':'polyline'})
    elif provider=='dgis':
        url='https://routing.api.2gis.com/routing/7.0.0/global?'+urlencode({'key':key})
        body={'points':[{'type':'stop','lat':a[0],'lon':a[1]},{'type':'stop','lat':b[0],'lon':b[1]}],'transport':'driving','route_mode':'fastest','traffic_mode':'jam','locale':'en'}
    elif provider=='google':
        url='https://routes.googleapis.com/directions/v2:computeRoutes'
        headers.update({'X-Goog-Api-Key':key,'X-Goog-FieldMask':'routes.duration,routes.staticDuration,routes.distanceMeters,routes.polyline.encodedPolyline'})
        body={'origin':{'location':{'latLng':{'latitude':a[0],'longitude':a[1]}}},'destination':{'location':{'latLng':{'latitude':b[0],'longitude':b[1]}}},'travelMode':'DRIVE','routingPreference':'TRAFFIC_AWARE_OPTIMAL'}
    elif provider=='here':
        url='https://router.hereapi.com/v8/routes?'+urlencode({'apiKey':key,'transportMode':'car','origin':f'{a[0]},{a[1]}','destination':f'{b[0]},{b[1]}','return':'summary,polyline','departureTime':datetime.now(timezone.utc).isoformat()})
    else: raise ValueError('Unknown provider')
    if body is not None:
        headers['Content-Type']='application/json'
    return Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers,method='POST' if body is not None else 'GET')

def extract(provider, data):
    if provider=='ors':
        first=data['features'][0]
        s=first['properties']['summary']
        return s['duration'],'',s['distance'],first['geometry']
    if provider=='tomtom':
        first=data['routes'][0]; s=first['summary']
        geometry={'type':'LineString','coordinates':[[p['longitude'],p['latitude']] for leg in first['legs'] for p in leg['points']]}
        return s['travelTimeInSeconds'],s.get('noTrafficTravelTimeInSeconds',''),s['lengthInMeters'],geometry
    if provider=='dgis':
        first=data['result'][0]
        return first['total_duration'],'',first['total_distance'],{'provider_format':'2GIS maneuvers','maneuvers':first.get('maneuvers',[])}
    if provider=='google':
        first=data['routes'][0]
        return float(first['duration'].rstrip('s')),float(first['staticDuration'].rstrip('s')) if first.get('staticDuration') else '',first['distanceMeters'],first.get('polyline',{})
    first=data['routes'][0]['sections']
    return sum(x['summary']['duration'] for x in first),'',sum(x['summary']['length'] for x in first),{'provider_format':'HERE flexible polylines','sections':[x.get('polyline','') for x in first]}

def fetch(provider, route, key, cycle, phase, directory, secrets):
    stamp=datetime.now(timezone.utc)
    row=dict.fromkeys(FIELDS,'')
    row.update(timestamp_utc=stamp.isoformat(),timestamp_astana=stamp.astimezone(ASTANA).isoformat(),cycle_id=cycle,route_id=route['route_id'],provider=provider,phase=phase,request_id=str(uuid.uuid4()))
    before=time.monotonic()
    payload=None
    try:
        with build_opener(NoRedirect()).open(build_request(provider,route,key),timeout=40) as response:
            row['http_code']=response.status
            payload=json.loads(response.read(8_000_000))
        duration,no_traffic,distance,geometry=extract(provider,payload)
        # Keep numeric returned measurements unchanged. Validation belongs in analysis.
        row.update(duration_s=duration,duration_no_traffic_s=no_traffic,distance_m=distance,status='ok')
        stem=f'{cycle}_{route["route_id"]}_{provider}'
        path=directory/'responses'/f'{stem}.json'; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(scrub(payload,secrets),ensure_ascii=False),encoding='utf-8')
        geom=directory/'geometry'/f'{stem}.json'; geom.parent.mkdir(parents=True,exist_ok=True)
        geom.write_text(json.dumps(scrub(geometry,secrets),ensure_ascii=False),encoding='utf-8')
        row['raw_file']=path.relative_to(ROOT).as_posix()
        row['geometry_file']=geom.relative_to(ROOT).as_posix()
    except HTTPError as error:
        row.update(status='http_error',http_code=error.code,error=f'HTTP_{error.code}')
        error.close()
    except (URLError,TimeoutError,OSError): row.update(status='transport_error',error='TransportError')
    except (KeyError,IndexError,TypeError,ValueError): row.update(status='invalid_response',error='UnexpectedSchema')
    row['latency_s']=round(time.monotonic()-before,3)
    return row

def log(who,what,wrong,changed):
    path=ROOT/'docs/research_log.md'
    with path.open('a',encoding='utf-8') as f:
        f.write(f'| {datetime.now(ASTANA).isoformat(timespec="seconds")} | {who} | {what} | {wrong} | {changed} |\n')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--mode',choices=['pilot','scheduled','manual','preflight'],default='manual')
    ap.add_argument('--cycle-label',default='')
    ap.add_argument('--dry-run',action='store_true')
    args=ap.parse_args()
    config=json.loads((ROOT/'config/study.json').read_text(encoding='utf-8'))
    now=datetime.now(timezone.utc)
    end=datetime.fromisoformat(config['collection_end_utc'])
    if args.mode in {'scheduled','manual'} and now>=end:
        print('Collection window ended. No requests sent.')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('expired=true\n')
        return
    routes=list(csv.DictReader((ROOT/'data/routes.csv').open(encoding='utf-8-sig',newline='')))
    statepath=ROOT/'data/collection_state.json'
    state=json.loads(statepath.read_text()) if statepath.exists() else {'next_route':0,'counts':{},'completed_cycles':[]}
    if args.mode in {'scheduled','manual'}:
        start=state['next_route']
        routes=[routes[(start+i)%len(routes)] for i in range(config['routes_per_scheduled_run'])]
    elif args.mode=='preflight':routes=routes[:1]
    providers=config['active_providers']
    for provider in providers:
        if config['providers'][provider]['collection_approved'] is not True:
            raise SystemExit('Provider collection is not approved in versioned config')
    keys=load_keys()
    if any(not keys[p] for p in providers):raise SystemExit('A configured credential is absent; no requests sent')
    cycle=args.cycle_label or f'{args.mode}-{now:%Y%m%dT%H%M%SZ}-{os.environ.get("GITHUB_RUN_ID",uuid.uuid4().hex[:8])}'
    if not all(c.isalnum() or c in '-_' for c in cycle):raise SystemExit('Unsafe cycle identifier')
    if cycle in state['completed_cycles']:raise SystemExit('Cycle already recorded; duplicate collection prevented')
    for p in providers:
        if state['counts'].get(p,0)+len(routes)>config['providers'][p]['study_request_budget']:
            raise SystemExit('Study request budget exhausted; no requests sent')
    if args.dry_run:
        print(json.dumps({'mode':args.mode,'routes':[r['route_id'] for r in routes],'providers':providers,'cycle':cycle}))
        return
    directory=ROOT/('data/preflight' if args.mode=='preflight' else 'data/raw')
    directory.mkdir(parents=True,exist_ok=True)
    csvpath=directory/f'{now:%Y-%m-%d}.csv'
    rows=[]
    spacing=max(config['providers'][p]['minimum_interval_s'] for p in providers)
    for index,route in enumerate(routes):
        tick=time.monotonic()
        with ThreadPoolExecutor(max_workers=len(providers)) as executor:
            requests=[executor.submit(fetch,p,route,keys[p],cycle,args.mode,directory,list(keys.values())) for p in providers]
            pair=[task.result() for task in requests]
        with csvpath.open('a',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=FIELDS)
            if f.tell()==0:writer.writeheader()
            writer.writerows(pair)
        rows.extend(pair)
        for p in providers:state['counts'][p]=state['counts'].get(p,0)+1
        # Persist attempted-request budget even if a later request crashes.
        statepath.write_text(json.dumps(state,indent=2),encoding='utf-8')
        print(json.dumps({'cycle':cycle,'route':route['route_id'],'statuses':{r['provider']:r['status'] for r in pair}}),flush=True)
        if any(r['http_code'] in (401,402,403,429) for r in pair):
            log('collector',f'{cycle}: stopped after {len(rows)} attempts','Credential/billing/rate-limit response','No retries; inspect status before restarting')
            break
        if index<len(routes)-1:time.sleep(max(0,spacing-(time.monotonic()-tick)))
    if args.mode in {'scheduled','manual'}:state['next_route']=(state['next_route']+len(routes))%config['route_count']
    state['completed_cycles'].append(cycle)
    statepath.write_text(json.dumps(state,indent=2),encoding='utf-8')
    success=sum(r['status']=='ok' for r in rows)
    log('GitHub Actions' if os.environ.get('GITHUB_ACTIONS') else 'local collector',f'{cycle}; {len(rows)} requests; {success} usable API responses',f'{len(rows)-success} unsuccessful requests','Raw values retained; no cleaning in collector')
    print(json.dumps({'cycle':cycle,'requests':len(rows),'success':success,'csv':csvpath.relative_to(ROOT).as_posix()}))

if __name__=='__main__':main()
