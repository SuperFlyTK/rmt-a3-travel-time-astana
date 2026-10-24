"""Collect public route estimates; never log credentials or follow redirects."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import csv
from datetime import datetime, timezone, timedelta
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote, quote_plus
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
        forbidden={'key','api_key','apikey','authorization','x-goog-api-key','access_token','token'}
        return {scrub(k,secrets):scrub(v,secrets) for k,v in value.items() if k.lower() not in forbidden}
    if isinstance(value,list): return [scrub(v,secrets) for v in value]
    if isinstance(value,str):
        for secret in secrets:
            if secret:
                for representation in {secret,quote(secret,safe=''),quote_plus(secret)}:
                    value=value.replace(representation,'[REDACTED]')
    return value

def build_request(provider, route, key):
    a=(float(route['origin_lat']),float(route['origin_lon']))
    b=(float(route['destination_lat']),float(route['destination_lon']))
    headers={'Accept':'application/json','User-Agent':'RMT-A3-Astana-Educational-Instrument/1.0'}
    body=None
    if provider=='ors':
        url='https://api.heigit.org/openrouteservice/v2/directions/driving-car/geojson'
        headers['Accept']='application/geo+json'
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
    except (URLError,TimeoutError,OSError,HTTPException): row.update(status='transport_error',error='TransportError')
    except (KeyError,IndexError,TypeError,ValueError): row.update(status='invalid_response',error='UnexpectedSchema')
    row['latency_s']=round(time.monotonic()-before,3)
    return row

def log(who,what,wrong,changed):
    path=ROOT/'docs/research_log.md'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as f:
        f.write(f'| {datetime.now(ASTANA).isoformat(timespec="seconds")} | {who} | {what} | {wrong} | {changed} |\n')

def atomic_state(path,state):
    """Never expose a half-written budget after process or machine interruption."""
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('w',encoding='utf-8') as handle:
            json.dump(state,handle,indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def collection_lock():
    """Fail closed on overlapping local collectors; inspect a crash-stale lock."""
    lock=ROOT/'data/.collector.lock'
    lock.parent.mkdir(parents=True,exist_ok=True)
    try:
        handle=lock.open('x',encoding='utf-8')
    except FileExistsError:
        raise SystemExit('Another collector holds data/.collector.lock; inspect before removing a stale lock')
    try:
        with handle:
            handle.write(json.dumps({'pid':os.getpid(),'created_at_utc':datetime.now(timezone.utc).isoformat()}))
        yield
    finally:
        lock.unlink(missing_ok=True)


def output_flag(name,value):
    path=os.environ.get('GITHUB_OUTPUT')
    if path:
        with open(path,'a',encoding='utf-8') as handle:handle.write(f'{name}={value}\n')


def reserve_plan(state,config,routes,mode,cycle,config_hash):
    """Conservatively reserve the entire plan before any HTTP request.

    Unused reservations are never refunded automatically. Actions commits this
    state to the remote repository before execution, surviving runner loss.
    """
    if state.get('halted_reason'):
        raise SystemExit('Collection is halted after a credential, billing, or rate-limit response; inspect state')
    reservations=state.setdefault('reservations',{})
    if cycle in reservations or cycle in state.setdefault('completed_cycles',[]):
        raise SystemExit('Cycle already reserved or completed; duplicate collection prevented')
    if mode in {'scheduled','manual'}:
        start=state.get('next_route',0)
        selected=[routes[(start+i)%len(routes)] for i in range(config['routes_per_scheduled_run'])]
    elif mode=='preflight':selected=routes[:1]
    else:selected=routes
    providers=config['active_providers']
    if not providers or not selected:raise SystemExit('No configured providers or routes; no requests sent')
    for p in providers:
        if config['providers'][p]['collection_approved'] is not True:
            raise SystemExit('Provider collection is not approved in versioned config')
        if state['counts'].get(p,0)+len(selected)>config['providers'][p]['study_request_budget']:
            raise SystemExit('Study request budget exhausted; no requests sent')
    plan={'cycle':cycle,'mode':mode,'providers':providers,'routes':selected,'status':'reserved',
          'reserved_at_utc':datetime.now(timezone.utc).isoformat(),'config_sha256':config_hash,
          'spacing_s':max(config['providers'][p]['minimum_interval_s'] for p in providers)}
    reservations[cycle]=plan
    for p in providers:state['counts'][p]=state['counts'].get(p,0)+len(selected)
    if mode in {'scheduled','manual'}:state['next_route']=(state.get('next_route',0)+len(selected))%len(routes)
    return plan


def execute_plan(state,plan,config,statepath,config_hash):
    if plan['status']!='reserved':raise SystemExit('Reservation already started or completed; never replay requests')
    if plan['config_sha256']!=config_hash:raise SystemExit('Configuration changed since reservation; no requests sent')
    if any(config['providers'][p]['collection_approved'] is not True for p in plan['providers']):
        raise SystemExit('Provider collection is no longer approved; no requests sent')
    keys=load_keys()
    if any(not keys[p] for p in plan['providers']):raise SystemExit('A configured credential is absent; no requests sent')
    plan['status']='started'
    plan['started_at_utc']=datetime.now(timezone.utc).isoformat()
    atomic_state(statepath,state)
    mode,cycle,routes,providers=plan['mode'],plan['cycle'],plan['routes'],plan['providers']
    now=datetime.now(timezone.utc)
    directory=ROOT/('data/preflight' if mode=='preflight' else 'data/raw')
    directory.mkdir(parents=True,exist_ok=True)
    csvpath=directory/f'{now:%Y-%m-%d}.csv'
    rows=[]
    for index,route in enumerate(routes):
        tick=time.monotonic()
        with ThreadPoolExecutor(max_workers=len(providers)) as executor:
            requests=[executor.submit(fetch,p,route,keys[p],cycle,mode,directory,list(keys.values())) for p in providers]
            pair=[task.result() for task in requests]
        with csvpath.open('a',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=FIELDS)
            if handle.tell()==0:writer.writeheader()
            writer.writerows(pair)
            handle.flush()
            os.fsync(handle.fileno())
        rows.extend(pair)
        plan['recorded_attempts']=len(rows)
        atomic_state(statepath,state)
        print(json.dumps({'cycle':cycle,'route':route['route_id'],'statuses':{r['provider']:r['status'] for r in pair}}),flush=True)
        if any(r['http_code'] in (401,402,403,429) for r in pair):
            state['halted_reason']='credential_billing_or_rate_limit_response'
            output_flag('halted','true')
            log('collector',f'{cycle}: stopped after {len(rows)} recorded attempts','Credential/billing/rate-limit response','Collection halted; reserved budget retained; no automatic retries')
            break
        if index<len(routes)-1:time.sleep(max(0,plan['spacing_s']-(time.monotonic()-tick)))
    state['completed_cycles'].append(cycle)
    plan['status']='completed'
    plan['completed_at_utc']=datetime.now(timezone.utc).isoformat()
    atomic_state(statepath,state)
    success=sum(r['status']=='ok' for r in rows)
    log('GitHub Actions' if os.environ.get('GITHUB_ACTIONS') else 'local collector',f'{cycle}; {len(rows)} recorded attempts; {success} API responses with expected schema',f'{len(rows)-success} unsuccessful requests','Raw values retained; full plan reserved before HTTP; unused reservation not refunded')
    print(json.dumps({'cycle':cycle,'requests':len(rows),'success':success,'csv':csvpath.relative_to(ROOT).as_posix()}))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--mode',choices=['pilot','scheduled','manual','preflight'],default='manual')
    ap.add_argument('--cycle-label',default='')
    ap.add_argument('--dry-run',action='store_true')
    ap.add_argument('--reserve-only',action='store_true')
    ap.add_argument('--execute-reservation',default='')
    args=ap.parse_args()
    if args.execute_reservation and (args.reserve_only or args.dry_run):ap.error('Reservation execution cannot be combined with reserve-only/dry-run')
    config_bytes=(ROOT/'config/study.json').read_bytes()
    config=json.loads(config_bytes.decode('utf-8-sig'))
    config_hash=hashlib.sha256(config_bytes).hexdigest()
    now=datetime.now(timezone.utc)
    end=datetime.fromisoformat(config['collection_end_utc'])
    if args.mode in {'scheduled','manual'} and now>=end:
        print('Collection window ended. No requests sent.')
        output_flag('expired','true')
        return
    with collection_lock():
        statepath=ROOT/'data/collection_state.json'
        state=json.loads(statepath.read_text()) if statepath.exists() else {'next_route':0,'counts':{},'completed_cycles':[]}
        if args.execute_reservation:
            plan=state.get('reservations',{}).get(args.execute_reservation)
            if not plan:raise SystemExit('Unknown reservation; no requests sent')
            if state.get('halted_reason'):raise SystemExit('Collection is halted; no requests sent')
            if plan['mode'] in {'scheduled','manual'} and now>=end:
                output_flag('expired','true')
                print('Collection window ended. Reserved budget retained; no requests sent.')
                return
            execute_plan(state,plan,config,statepath,config_hash)
            return
        with (ROOT/'data/routes.csv').open(encoding='utf-8-sig',newline='') as handle:routes=list(csv.DictReader(handle))
        cycle=args.cycle_label or f'{args.mode}-{now:%Y%m%dT%H%M%SZ}-{os.environ.get("GITHUB_RUN_ID",uuid.uuid4().hex[:8])}'
        if not cycle or not all(c.isalnum() or c in '-_' for c in cycle):raise SystemExit('Unsafe cycle identifier')
        if not routes:raise SystemExit('Route register is empty; no requests sent')
        plan=reserve_plan(state,config,routes,args.mode,cycle,config_hash)
        if args.dry_run:
            print(json.dumps({'mode':args.mode,'routes':[r['route_id'] for r in plan['routes']],'providers':plan['providers'],'cycle':cycle}))
            return
        atomic_state(statepath,state)
        if args.reserve_only:
            output_flag('reservation_id',cycle)
            print(json.dumps({'cycle':cycle,'reserved_requests_per_provider':len(plan['routes']),'providers':plan['providers']}))
            return
        execute_plan(state,plan,config,statepath,config_hash)

if __name__=='__main__':main()
