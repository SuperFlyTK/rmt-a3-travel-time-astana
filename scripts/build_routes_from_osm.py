"""Rebuild fixed OD pairs from the preserved OSM snapshot, without network calls."""
from pathlib import Path
from math import radians, sin, cos, asin, sqrt
from statistics import median
import csv, json

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'data' / 'sources'
raw = json.loads((SRC / 'astana_osm_overpass.json').read_text(encoding='utf-8'))
elements = raw['elements']

def metres(a, b):
    lat1, lon1, lat2, lon2 = map(radians, (a['lat'], a['lon'], b['lat'], b['lon']))
    term = sin((lat2-lat1)/2)**2 + cos(lat1)*cos(lat2)*sin((lon2-lon1)/2)**2
    return 6371008.8 * 2 * asin(sqrt(min(1, term)))

def element(kind, ident):
    return next(e for e in elements if e['type'] == kind and e['id'] == ident)

def road_nodes(name):
    nodes = {}
    for way in elements:
        tags = way.get('tags', {})
        if way['type'] != 'way' or tags.get('name') != name:
            continue
        if tags.get('highway') not in {'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential'}:
            continue
        if tags.get('access') in {'no', 'private'} or tags.get('motor_vehicle') == 'no':
            continue
        for nid, g in zip(way['nodes'], way['geometry']):
            nodes.setdefault(nid, {**g, 'node_id':nid, 'way_id':way['id'], 'highway':tags['highway']})
    if not nodes:
        raise ValueError('No eligible road geometry: '+name)
    return list(nodes.values())

def centroid_hint(way_id):
    geom = element('way', way_id)['geometry']
    return {'lat':sum(g['lat'] for g in geom)/len(geom), 'lon':sum(g['lon'] for g in geom)/len(geom)}

endpoints = []
def select_road(eid, label, name, landmark_way=None):
    candidates = road_nodes(name)
    hint = centroid_hint(landmark_way) if landmark_way else {'lat':median(g['lat'] for g in candidates), 'lon':median(g['lon'] for g in candidates)}
    chosen = min(candidates, key=lambda g:(metres(g,hint),g['node_id']))
    endpoints.append({'endpoint_id':eid,'label':label,'lat':chosen['lat'],'lon':chosen['lon'],
        'source_url':f"https://www.openstreetmap.org/node/{chosen['node_id']}",
        'supporting_way_url':f"https://www.openstreetmap.org/way/{chosen['way_id']}",
        'osm_road_name':name,'osm_highway':chosen['highway'],
        'selection_rule':'Existing road geometry node nearest arithmetic mean of landmark vertices' if landmark_way else 'Existing road geometry node nearest component-wise median of unique eligible road nodes',
        'landmark_source':f'https://www.openstreetmap.org/way/{landmark_way}' if landmark_way else '',
        'distance_to_selection_hint_m':round(metres(chosen,hint),1),
        'caution':'Road vicinity; not a verified building entrance or guaranteed route through the named artery' if landmark_way else 'Named road endpoint; no route-through requirement'})

def select_stop(eid,label,node_id):
    e=element('node',node_id)
    assert e['tags'].get('highway')=='bus_stop'
    endpoints.append({'endpoint_id':eid,'label':label,'lat':e['lat'],'lon':e['lon'],
        'source_url':f'https://www.openstreetmap.org/node/{node_id}',
        'supporting_way_url':'','osm_road_name':'','osm_highway':'bus_stop',
        'selection_rule':'Exact coordinates of named public bus-stop OSM node',
        'landmark_source':f'https://www.openstreetmap.org/node/{node_id}',
        'distance_to_selection_hint_m':0,
        'caution':'Public roadside vicinity; provider snapping may differ; not a railway platform or building entrance'})

select_road('P01','Turan Avenue near Khan Shatyr','Тұран даңғылы',460703779)
select_road('P02','Kabanbay Batyr Avenue near airport T1','Қабанбай Батыр даңғылы',166500591)
select_road('P03','Mangilik El Avenue near EXPO exhibition centre','Мәңгілік Ел даңғылы',454853233)
select_road('P04','Saryarka Avenue central OSM road node','Сарыарқа даңғылы')
select_road('P05','Abay Avenue central OSM road node','Абай даңғылы')
select_road('P06','Bogenbay Batyr Avenue central OSM road node','Бөгенбай Батыр даңғылы')
select_road('P07','Kenesary Street central OSM road node','Кенесары көшесі')
select_road('P08','Syganak Street central OSM road node','Сығанақ көшесі')
select_stop('P09','Nurly Zhol railway-station bus-stop vicinity',4894180331)
select_stop('P10','National EXPO Museum bus-stop vicinity',13926684562)
points={e['endpoint_id']:e for e in endpoints}
undirected=[('P01','P02'),('P03','P09'),('P01','P04'),('P03','P05'),('P06','P02'),('P07','P08'),('P04','P09'),('P05','P10'),('P06','P08'),('P07','P09')]
routes=[]
for pair in undirected:
    for a,b in (pair,pair[::-1]):
        origin,destination=points[a],points[b]
        routes.append({'route_id':f'R{len(routes)+1:02d}','name':origin['label']+' -> '+destination['label'],
            'origin_lat':origin['lat'],'origin_lon':origin['lon'],'destination_lat':destination['lat'],'destination_lon':destination['lon'],
            'expected_length_m':round(metres(origin,destination),1),
            'expected_length_basis':'haversine_spherical_straight_line_lower_bound_R6371008.8m_not_observed_route_length',
            'origin_source':origin['source_url'],'destination_source':destination['source_url']})
assert len(routes)==20 and len({(r['origin_lat'],r['origin_lon'],r['destination_lat'],r['destination_lon']) for r in routes})==20
assert len(points)==10 and len({(p['lat'],p['lon']) for p in endpoints})==10
assert all(r['expected_length_m']>1000 for r in routes)
for filename,rows in [(SRC/'selected_endpoints.csv',endpoints),(ROOT/'data'/'routes.csv',routes)]:
    with filename.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
print(json.dumps({'routes':len(routes),'endpoints':len(points),'min_lower_bound_m':min(r['expected_length_m'] for r in routes),'max_lower_bound_m':max(r['expected_length_m'] for r in routes),'endpoint_details':endpoints},ensure_ascii=False,indent=2))
