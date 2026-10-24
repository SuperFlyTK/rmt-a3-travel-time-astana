"""Describe published window-level benchmark outputs; never invent route data."""
from pathlib import Path
from datetime import datetime,timezone
import csv,json,math,statistics

ROOT=Path(__file__).resolve().parents[1]

def summarise(rows,protocol):
    ids={x['id']:x for x in protocol['windows']}
    seen=set(); valid=[]
    for row in rows:
        cid=row['cycle_id']
        if cid in seen or cid not in ids:raise ValueError('Duplicate or unregistered follow-up cycle')
        seen.add(cid)
        if row['period'] != ids[cid]['period']:raise ValueError('Period conflicts with protocol')
        if int(row['n_valid_pairs'])>=5 and row['median_absolute_difference_s']!='':
            value=float(row['median_absolute_difference_s'])
            if not math.isfinite(value) or value<0:raise ValueError('Invalid aggregate')
            valid.append(row)
    groups=[]
    for period in ['peak','offpeak']:
        group=[x for x in valid if x['period']==period]
        values=[float(x['median_absolute_difference_s']) for x in group]
        groups.append({'period':period,'planned_windows':sum(x['period']==period for x in ids.values()),'recorded_windows':sum(x['period']==period for x in rows),'valid_windows':len(group),'n_valid_pairs':sum(int(x['n_valid_pairs']) for x in group),'median_of_window_median_absolute_differences_s':statistics.median(values) if values else None})
    peak,offpeak=[x['median_of_window_median_absolute_differences_s'] for x in groups]
    return {'generated_at_utc':datetime.now(timezone.utc).isoformat(),'planned_windows':len(ids),'recorded_windows':len(rows),'valid_windows':len(valid),'n_valid_pairs':sum(int(x['n_valid_pairs']) for x in valid),'groups':groups,'peak_minus_offpeak_window_median_s':peak-offpeak if peak is not None and offpeak is not None else None,'peak_to_offpeak_window_median_ratio':peak/offpeak if peak is not None and offpeak is not None and offpeak>0 else None,'inference':'not_run_aggregate_windows_do_not_recover_route_day_pairs','interpretation':'Descriptive window-level comparison only; traffic-aware Google versus non-live-traffic ORS control; no accuracy or causal claim. Original TomTom-2GIS expected results are not tested by these observations.'}

def main():
    protocol=json.loads((ROOT/'docs/google_followup_protocol.json').read_text(encoding='utf-8'))
    source=ROOT/'results/google_followup_benchmark.csv'
    rows=list(csv.DictReader(source.open(encoding='utf-8-sig',newline=''))) if source.exists() else []
    result=summarise(rows,protocol)
    (ROOT/'results/google_followup_summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    with (ROOT/'results/google_followup_period_summary.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(result['groups'][0]));writer.writeheader();writer.writerows(result['groups'])
    print(json.dumps({'planned_windows':result['planned_windows'],'recorded_windows':result['recorded_windows'],'n_valid_pairs':result['n_valid_pairs']}))

if __name__=='__main__':main()
