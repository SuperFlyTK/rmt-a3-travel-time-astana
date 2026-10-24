"""Plot one complete ORS cycle using only saved geometry JSON and routes.csv.

No network access, basemap tiles, API keys or observed GPS traces are used.
Example: python scripts/route_map.py --cycle pilot-1
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import patheffects


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_routes(path: Path) -> list[dict]:
    with path.open(newline='', encoding='utf-8-sig') as handle:
        routes = list(csv.DictReader(handle))
    required = {'route_id','name','origin_lat','origin_lon','destination_lat','destination_lon'}
    if not routes or not required.issubset(routes[0]):
        raise ValueError('Route register is empty or missing required columns.')
    if len(routes) != 20 or len({r['route_id'] for r in routes}) != 20:
        raise ValueError('Expected exactly 20 distinct registered route IDs.')
    for row in routes:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', row['route_id']):
            raise ValueError('Invalid route ID filename component.')
        for key in ('origin_lat','origin_lon','destination_lat','destination_lon'):
            row[key] = float(row[key])
            if not math.isfinite(row[key]):
                raise ValueError('Nonfinite endpoint coordinate in route register.')
    return routes


def read_geometry(path: Path) -> list[tuple[float,float]]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('type') != 'LineString':
        raise ValueError(f'{path.name}: expected a saved ORS GeoJSON LineString.')
    coords = data.get('coordinates', [])
    if len(coords) < 2:
        raise ValueError(f'{path.name}: geometry needs at least two positions.')
    positions = []
    for point in coords:
        if not isinstance(point, list) or len(point) < 2:
            raise ValueError(f'{path.name}: invalid position.')
        lon, lat = float(point[0]), float(point[1])
        if not all(math.isfinite(v) for v in (lon,lat)) or not (-180 <= lon <=180 and -90 <= lat <=90):
            raise ValueError(f'{path.name}: invalid longitude/latitude.')
        positions.append((lon,lat))
    return positions


def endpoint_key(route: dict):
    return (route['origin_lat'],route['origin_lon'],route['destination_lat'],route['destination_lon'])


def reversed_pairs(routes: list[dict]):
    by_endpoints = {endpoint_key(r):r for r in routes}
    if len(by_endpoints) != len(routes):
        raise ValueError('Duplicate directed OD pair in route register.')
    seen, pairs = set(), []
    for route in sorted(routes,key=lambda r:r['route_id']):
        if route['route_id'] in seen:
            continue
        key = endpoint_key(route)
        reverse = by_endpoints.get((key[2],key[3],key[0],key[1]))
        if reverse is None or reverse['route_id']==route['route_id']:
            raise ValueError('Every route must have a distinct registered reverse direction.')
        pairs.append((route,reverse))
        seen.update((route['route_id'],reverse['route_id']))
    if len(pairs)!=10:
        raise ValueError('Expected ten pairs of reverse directions.')
    return pairs


def point_along(coords, fraction, cos_lat):
    """Locate a display anchor along the actual polyline; do not alter its path."""
    lengths=[math.hypot((b[0]-a[0])*cos_lat,b[1]-a[1]) for a,b in zip(coords,coords[1:])]
    total=sum(lengths)
    if total<=0:
        raise ValueError('Degenerate geometry with zero plotted length.')
    target=total*fraction
    travelled=0.0
    for a,b,length in zip(coords,coords[1:],lengths):
        if length>0 and travelled+length>=target:
            weight=(target-travelled)/length
            return (a[0]+weight*(b[0]-a[0]),a[1]+weight*(b[1]-a[1]))
        travelled+=length
    return coords[-1]


def render(root: Path, cycle: str):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',cycle):
        raise ValueError('Cycle must be a filename-safe identifier.')
    register=root/'data'/'routes.csv'
    routes=read_routes(register)
    pairs=reversed_pairs(routes)
    geometry_dir=root/'data'/'raw'/'geometry'
    files={r['route_id']:geometry_dir/f"{cycle}_{r['route_id']}_ors.json" for r in routes}
    missing=[p.name for p in files.values() if not p.is_file()]
    if missing:
        raise ValueError(f'Cycle {cycle} is incomplete: {len(missing)} of 20 ORS geometry files missing. No figure written. Missing: '+', '.join(missing))
    geometries={rid:read_geometry(path) for rid,path in files.items()}
    all_coords=[point for line in geometries.values() for point in line]
    min_lon,max_lon=min(p[0] for p in all_coords),max(p[0] for p in all_coords)
    min_lat,max_lat=min(p[1] for p in all_coords),max(p[1] for p in all_coords)
    cos_lat=math.cos(math.radians((min_lat+max_lat)/2))
    if not 0<cos_lat<=1:
        raise ValueError('Latitude extent unsuitable for this Astana display.')
    if not all(50.5<=lat<=51.6 and 70.9<=lon<=72.1 for lon,lat in all_coords):
        raise ValueError('Geometry lies outside the broad Astana display bounds; inspect the source before plotting.')

    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.titlesize':12,'axes.labelsize':10})
    fig, axes=plt.subplots(1,2,figsize=(12.4,8.6),sharex=True,sharey=True)
    fig.patch.set_facecolor('white')
    colours=plt.get_cmap('tab10').colors
    label_boxes=[[] for _ in axes]
    for index,(forward,reverse) in enumerate(pairs):
        colour=colours[index]
        for panel,route in enumerate((forward,reverse)):
            ax=axes[panel];coords=geometries[route['route_id']]
            x,y=zip(*coords)
            ax.plot(x,y,color=colour,lw=1.75,alpha=.80,zorder=2)
            ax.scatter([x[0]],[y[0]],marker='o',s=20,color=colour,edgecolor='white',linewidth=.6,zorder=4)
            ax.scatter([x[-1]],[y[-1]],marker='s',s=19,color=colour,edgecolor='white',linewidth=.6,zorder=4)
            a=point_along(coords,.46,cos_lat);b=point_along(coords,.53,cos_lat)
            ax.annotate('',xy=b,xytext=a,arrowprops={'arrowstyle':'-|>','color':colour,'lw':1.3,'mutation_scale':11},zorder=5)
            # Pick a label from actual line positions, favouring separation from earlier labels.
            candidates=[point_along(coords,f,cos_lat) for f in (.20,.32,.44,.56,.68,.80)]
            anchors=label_boxes[panel]
            def clearance(point):
                return min((math.hypot((point[0]-other[0])*cos_lat,point[1]-other[1]) for other in anchors),default=1)
            anchor=max(candidates,key=clearance)
            anchors.append(anchor)
            text=ax.annotate(route['route_id'],xy=anchor,xytext=(4,4 if index%2==0 else -10),textcoords='offset points',
                             fontsize=9,color=colour,fontweight='bold',zorder=8,
                             bbox={'boxstyle':'round,pad=.15','fc':'white','ec':'none','alpha':.88})
            text.set_path_effects([patheffects.withStroke(linewidth=.25,foreground='white')])
    pad_lon=max((max_lon-min_lon)*.09,.005)
    pad_lat=max((max_lat-min_lat)*.07,.005)
    for panel,ax in enumerate(axes):
        ax.set_title('First direction of each OD pair' if panel==0 else 'Registered reverse directions',pad=10)
        ax.set(xlim=(min_lon-pad_lon,max_lon+pad_lon),ylim=(min_lat-pad_lat,max_lat+pad_lat),xlabel='Longitude (degrees east)')
        ax.set_aspect(1/cos_lat)
        ax.grid(color='#dce3e8',lw=.5,alpha=.8)
        ax.set_axisbelow(True)
        ax.ticklabel_format(style='plain',useOffset=False)
        for spine in ax.spines.values():spine.set_color('#b4c0c9')
    axes[0].set_ylabel('Latitude (degrees north)')
    fig.suptitle('ORS control route geometries',fontsize=17,fontweight='bold',y=.975)
    fig.text(.5,.935,f'Astana | {cycle} | 20 saved provider routes | no live traffic | not observed GPS trajectories',ha='center',fontsize=10,color='#334155')
    fig.text(.5,.095,'Same colour = reversed OD pair. Circle = origin; square = destination. Arrows indicate travel direction.',ha='center',fontsize=9,color='#334155')
    fig.text(.5,.069,'Both panels use the same extent. Displayed lines are saved ORS recommendations; no basemap or invented connectors.',ha='center',fontsize=8.6,color='#334155')
    fig.text(.5,.035,'Routing geometry: © openrouteservice.org by HeiGIT | CC BY-SA 4.0\nMap data and fixed endpoints: © OpenStreetMap contributors | ODbL 1.0',ha='center',fontsize=8,color='#475569',linespacing=1.5)
    fig.subplots_adjust(left=.075,right=.97,top=.88,bottom=.15,wspace=.12)
    output=root/'figures';output.mkdir(parents=True,exist_ok=True)
    png=output/'route_map.png'
    fig.savefig(png,dpi=190,facecolor='white')
    plt.close(fig)
    geometry_sources=[path.relative_to(root).as_posix() for path in files.values()]
    caption=(f'ORS control route geometries for the complete {cycle} cycle: 20 directed routes displayed as ten forward and ten reverse OD directions on identical scales. '
             'Lines are actual saved openrouteservice LineString responses, not live-traffic routes or observed GPS trajectories. '
             'Endpoint labels follow data/routes.csv; recommended routes are not constrained to specified arteries. '
             'Routing geometry attribution: openrouteservice.org by HeiGIT, CC BY-SA 4.0; map data and endpoints: OpenStreetMap contributors, ODbL 1.0.')
    figure={'status':'created','file':'route_map.png','path':'figures/route_map.png','caption':caption,
            'source':'Saved ORS control geometry files and fixed OSM-derived OD register only',
            'source_files':['data/routes.csv']+geometry_sources,'n_geometry_files':20,'cycle_id':cycle,'provider':'ors',
            'route_ids':sorted(geometries),'live_traffic':False,'observed_gps':False}
    manifest={'generated_at_utc':datetime.now(timezone.utc).isoformat(),'matplotlib_version':matplotlib.__version__,
              'script_sha256':digest(Path(__file__)),'input_hashes':{'data/routes.csv':digest(register),**{p.relative_to(root).as_posix():digest(p) for p in files.values()}},
              'figure_sha256':digest(png),'figures':[figure],
              'attribution':{'ors':'© openrouteservice.org by HeiGIT','ors_license':'CC BY-SA 4.0','ors_license_url':'https://creativecommons.org/licenses/by-sa/4.0/',
                             'osm':'© OpenStreetMap contributors','osm_license':'ODbL 1.0','osm_copyright_url':'https://www.openstreetmap.org/copyright'}}
    manifest_path=output/'route_map_manifest.json'
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'figure':str(png),'manifest':str(manifest_path),'cycle':cycle,'geometry_files':20}))
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--cycle',default='pilot-1')
    args=parser.parse_args()
    try:
        render(args.root.resolve(),args.cycle)
    except (ValueError,OSError,json.JSONDecodeError) as error:
        parser.exit(2,f'Route map not created: {error}\n')
