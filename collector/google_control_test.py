"""Bounded exploratory Google/ORS benchmark; never persist Google route outputs."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime,timezone
import hashlib,json,math,os,statistics,time
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.request import build_opener
from collector.collect import ROOT,FIELDS,NoRedirect,build_request,load_keys,fetch,collection_lock,atomic_state,reserve_plan,log

CYCLE='google-control-1'
PROTOCOL=ROOT/'docs/google_control_protocol.json'

def google_request(route,key):
    started=datetime.now(timezone.utc)
    record={'timestamp_utc':started.isoformat(),'route_id':route['route_id'],'provider':'google_demo','http_code':'','status':'transport_error'}
    try:
        request=build_request('google',route,key)
        request.add_header('X-Goog-FieldMask','routes.duration,routes.distanceMeters')
        with build_opener(NoRedirect()).open(request,timeout=30) as response:
            payload=json.loads(response.read(1000000))
            record['http_code']=response.status
        first=payload['routes'][0]
        duration=float(first['duration'].removesuffix('s')); distance=float(first['distanceMeters'])
        if not (math.isfinite(duration) and math.isfinite(distance) and duration>0 and distance>0):raise ValueError('Invalid values')
        record['status']='ok'
        return record,(duration,distance)
    except HTTPError as exc:
        record.update(http_code=exc.code,status='http_error');exc.close()
    except (URLError,TimeoutError,OSError):pass
    except (KeyError,IndexError,ValueError,TypeError):record['status']='invalid_response'
    return record,None

def aggregate(pairs):
    """Input tuples are transient Google duration/distance and ORS duration/distance."""
    n=len(pairs)
    out={'n_valid_pairs':n,'median_signed_difference_s':'','median_absolute_difference_s':'','mae_s':'','rmse_s':'','smape_pct':'','iqr_signed_difference_s':'','n_length_comparable':'','length_comparable_pct':''}
    if n<5:return out
    differences=[a-c for a,b,c,d in pairs]
    absolute=[abs(v) for v in differences]
    q=statistics.quantiles(differences,n=4,method='inclusive')
    comparable=sum(100*abs(b-d)/((b+d)/2)<=5 for a,b,c,d in pairs)
    out.update(median_signed_difference_s=statistics.median(differences),median_absolute_difference_s=statistics.median(absolute),mae_s=statistics.mean(absolute),rmse_s=math.sqrt(statistics.mean([v*v for v in differences])),smape_pct=statistics.mean([100*abs(a-c)/((a+c)/2) for a,b,c,d in pairs]),iqr_signed_difference_s=q[2]-q[0],n_length_comparable=comparable,length_comparable_pct=100*comparable/n)
    return out

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--reserve-only',action='store_true');args=parser.parse_args()
    protocol=json.loads(PROTOCOL.read_text(encoding='utf-8'))
    assert protocol['max_pairs']==20 and protocol['publish_google_raw'] is False
    config_bytes=(ROOT/'config/study.json').read_bytes();config=json.loads(config_bytes)
    routes=list(csv.DictReader((ROOT/'data/routes.csv').open(encoding='utf-8-sig',newline='')))[:protocol['max_pairs']]
    statepath=ROOT/'data/collection_state.json'
    with collection_lock():
        state=json.loads(statepath.read_text(encoding='utf-8'))
        if args.reserve_only:
            plan=reserve_plan(state,config,routes,'exploratory',CYCLE,hashlib.sha256(config_bytes).hexdigest())
            plan['google_demo_reserved_requests']=len(routes)
            plan['protocol_sha256']=hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
            state['counts']['google_demo']=state['counts'].get('google_demo',1)+len(routes)
            atomic_state(statepath,state)
            print(json.dumps({'reserved_pairs':len(routes),'cycle':CYCLE}));return
        plan=state['reservations'][CYCLE]
        assert plan['status']=='reserved' and plan['protocol_sha256']==hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
        assert datetime.now(timezone.utc)<datetime.fromisoformat(config['collection_end_utc'])
        env=dict(line.split('=',1) for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.startswith('#'))
        key=env['GOOGLE_MAPS_DEMO_KEY'].strip();keys=load_keys()
        directory=ROOT/'data/exploratory/ors';directory.mkdir(parents=True,exist_ok=True)
        plan.update(status='started',started_at_utc=datetime.now(timezone.utc).isoformat());atomic_state(statepath,state)
        pairs=[];statuses=[];ors_rows=[];gaps=[]
        for route in routes:
            tick=time.monotonic()
            if datetime.now(timezone.utc)>=datetime.fromisoformat(config['collection_end_utc']):break
            with ThreadPoolExecutor(max_workers=2) as executor:
                g=executor.submit(google_request,route,key)
                o=executor.submit(fetch,'ors',route,keys['ors'],CYCLE,'exploratory',directory,[*keys.values(),key])
                google,values=g.result();ors=o.result()
            statuses.append(google);ors_rows.append(ors)
            gap=abs((datetime.fromisoformat(google['timestamp_utc'])-datetime.fromisoformat(ors['timestamp_utc'])).total_seconds())
            gaps.append(gap)
            if values and ors['status']=='ok' and gap<=60:
                pairs.append((*values,float(ors['duration_s']),float(ors['distance_m'])))
            with (directory/'observations.csv').open('a',encoding='utf-8',newline='') as handle:
                writer=csv.DictWriter(handle,fieldnames=FIELDS)
                if handle.tell()==0:writer.writeheader()
                writer.writerow(ors);handle.flush();os.fsync(handle.fileno())
            plan['recorded_attempts']=len(ors_rows);atomic_state(statepath,state)
            print(json.dumps({'route':route['route_id'],'google':google['status'],'ors':ors['status']}),flush=True)
            if google['http_code'] in (401,402,403,429) or ors['http_code'] in (401,402,403,429):
                if ors['http_code'] in (401,402,403,429):state['halted_reason']='credential_billing_or_rate_limit_response'
                break
            time.sleep(max(0,2.1-(time.monotonic()-tick)))
        result={'cycle_id':CYCLE,'start_utc':plan['started_at_utc'],'end_utc':datetime.now(timezone.utc).isoformat(),'n_attempted_pairs':len(statuses),'n_google_ok':sum(x['status']=='ok' for x in statuses),'n_ors_ok':sum(x['status']=='ok' for x in ors_rows),'max_request_start_gap_s':max(gaps,default=0),**aggregate(pairs)}
        out=ROOT/'results/google_control_benchmark.csv'
        with out.open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(result));writer.writeheader();writer.writerow(result)
        with (ROOT/'data/exploratory/google_request_status.csv').open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(statuses[0]) if statuses else ['timestamp_utc','route_id','provider','http_code','status']);writer.writeheader();writer.writerows(statuses)
        pairs.clear();values=None
        plan.update(status='completed',completed_at_utc=datetime.now(timezone.utc).isoformat());state['completed_cycles'].append(CYCLE);atomic_state(statepath,state)
        log('local exploratory test',f'{CYCLE}: {len(statuses)} attempted Google-ORS pairs','Separate control comparison; no inference','Only aggregate Google comparison results retained; ORS raw evidence separate; original expectations unchanged')
        print(json.dumps(result))

if __name__=='__main__':main()
