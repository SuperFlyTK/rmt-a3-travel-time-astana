# Astana endpoint source provenance

The route catalogue contains 20 directed origin-destination pairs, formed from 10 purposively selected public locations and 10 undirected pairs with their reversals. It was constructed before any research routing requests. No routing API was called to create it.

## Source and licence

Source: OpenStreetMap, retrieved through `https://overpass-api.de/api/interpreter` in one bounding-box query. Copyright and attribution: © OpenStreetMap contributors. Database licence: Open Database License (ODbL) 1.0.

- Copyright and attribution terms: https://www.openstreetmap.org/copyright
- ODbL licence: https://opendatacommons.org/licenses/odbl/1-0/
- Exact request: `astana_overpass_query.txt`
- Unmodified response: `astana_osm_overpass.json`
- Retrieval times, source database time, HTTP status and SHA256: `astana_osm_manifest.json`
- Exact selected nodes, supporting road ways, landmark objects and selection rules: `selected_endpoints.csv`

The query covers latitude 50.99 to 51.25 and longitude 71.25 to 71.70, and retrieves matching named roads and public landmarks with their geometries. Unrelated objects returned by the broad name filter were not used as endpoints. The source snapshot is retained for reproducibility; changes to live OSM objects do not change the fixed coordinates in this catalogue.

## Endpoint selection

Every endpoint coordinate is copied exactly from a retrieved OSM node or a retrieved way's node geometry. No coordinate was invented or interpolated. Eight points lie on eligible named motor-road geometries for Turan, Kabanbay Batyr, Mangilik El, Saryarka, Abay, Bogenbay Batyr, Kenesary and Syganak. Road candidates tagged private/no access or motor_vehicle=no were excluded when those tags were present. Absence of such tags is not independent proof of unrestricted access.

Three of those road nodes were selected nearest to the arithmetic mean of the mapped vertices of Khan Shatyr, airport T1, and an EXPO exhibition centre, respectively. These means were selection hints only; the output coordinates remain actual road nodes. The points are approximately 204 m, 112 m and 311 m from those hints. They represent the landmark vicinity, not a verified entrance. The other five arterial points were selected nearest to the component-wise median of unique nodes on the corresponding named road within the query area. This is a reproducible, purposive coverage rule, not random or representative sampling.

The remaining two points are exact named public bus-stop nodes: `Вокзал Нурлы Жол` and `Национальный музей Экспо`. These are roadside vicinities, not rail platforms or building entrances. The query also returned Nurly Zhol LRT objects; those were deliberately not substituted for the railway-station bus-stop point.

The pairs provide endpoint coverage of the named arteries and airport, Nurly Zhol, Khan Shatyr and EXPO locations. They do not guarantee that a provider's recommended route follows a specified artery or crosses a particular bridge. No via-point or route-through constraints are imposed. Returned geometries and provider snapping must be inspected separately during the actual pilot. Any subsequent endpoint amendment needs a dated log entry and a new catalogue version.

## Distance column

`expected_length_m` is a spherical haversine straight-line lower-bound approximation between the requested endpoints, with radius 6,371,008.8 m. It is not an observed road distance, routing prediction, or guaranteed numerical lower bound for a provider's route after endpoint snapping. Small differences also arise from the spherical Earth approximation. `expected_length_basis` declares this basis on every row.

No route durations, traffic measurements or provider outcomes are contained in this source catalogue. Reverse directions share the same straight-line distance but can produce different real driving routes and times.

## Rebuilding

Run `python scripts/build_routes_from_osm.py` from the repository using Python 3.11 or later. It reads the preserved JSON, applies the recorded selection rules, and regenerates `data/routes.csv` and `data/sources/selected_endpoints.csv` without network access. It verifies 20 distinct directed pairs, 10 distinct endpoints and positive lower-bound distances above 1 km. The ten undirected pair choices are explicit in the script.
