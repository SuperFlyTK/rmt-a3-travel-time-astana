# Astana route estimate study

Group SE-2424. Akhanov Yerdaulet, Shadiyarbek Syrym, Sabyrzhan Akylbek.

This repository contains a reproducible instrument pilot for RMT Assignment 3. The active collector uses **openrouteservice (ORS), without live traffic**. It does not yet answer the proposed TomTom versus 2GIS traffic-aware research question. TomTom comparison needs written approval under its current terms; 2GIS storage and publication rights for the Kazakhstan demo account remain unresolved. Google returned 403 and its trial requires a prepayment. HERE has no key. These limits are recorded rather than treated as successful collection.

Twenty directed origin-destination pairs were selected from public OpenStreetMap data. A full local pilot comprises three cycles with a five-minute pause between completed cycles. GitHub Actions samples four rotating routes every fifteen minutes, giving a nominal seventy-five-minute revisit per route. The schedule stops after the fixed forty-eight-hour window in `config/study.json`. GitHub scheduling is best effort; actual timestamps are preserved and missed observations are never invented.

## Reproduce

The delivered report uses a frozen sample of 64 ORS observations: 60 local pilot requests and 4 requests from a manually triggered GitHub Actions verification run. The collector labels that cloud run with its `scheduled` collection phase; it does not prove a cron event has fired. To reproduce the delivered sample exactly, follow [the frozen-snapshot instructions](docs/reproduce_report.md). The commands below instead analyze all observations currently present in the live data folder.

The frozen CSV is stored without Git newline conversion, so its SHA-256 matches its manifest on every platform. Other input hashes in the report snapshot describe the Windows workspace bytes at build time; Git or another operating system may change text line endings without changing data values. The preregistration checker compares normalized line endings explicitly.

Use Python 3.11 or newer. On Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe analysis/process.py
.\.venv\Scripts\python.exe analysis/figures.py
.\.venv\Scripts\python.exe report/build_report.py
```

Collection requires an `ORS_KEY` environment variable or local `.env`. Credentials must never be committed. An additional manual network cycle consumes four real API requests:

```powershell
.\.venv\Scripts\python.exe collector/collect.py --mode manual
```

Do not repeat the pilot under the same identifiers or extend the collection window without a dated protocol amendment. Enabling additional providers requires both a working key and the applicable collection/storage/publication rights. The collector rejects providers not approved in the versioned configuration.

## Files

- `config/study.json`: fixed periods, route count, budgets and collection window.
- `docs/expected_results.md` and `.json`: expectations committed before the instrument pilot; do not rewrite after observing results.
- `docs/research_log.md`: actual dated sessions and failures.
- `docs/api_check.md`: provider access, documented parameters, quotas and limitations.
- `data/routes.csv`: fixed public endpoints; expected length is a straight-line lower bound, not measured road distance.
- `data/sources/`: OSM query, snapshot, source manifest, selected endpoints and licensing.
- `data/raw/`: immutable request observations, returned ORS geometry and responses.
- `data/collection_state.json`: persisted request reservations and rotating route cursor.
- `results/`: generated quality checks, dictionary, coverage and descriptive results.
- `analysis/`: deterministic processing and charts, with tests in `tests/`.
- `collector/`: API adapters, safe collection and local pilot driver.
- `report/`: generated report builder; the named DOCX is in the project root.
- `figures/`: original study evidence and graphics.
- `.github/workflows/collect.yml`: bounded cloud collection with secrets supplied by GitHub.

## Data attribution and interpretation

ORS results: **© openrouteservice by HeiGIT | Data from OpenStreetMap**, licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). See [ORS terms](https://account.heigit.org/info/tos). The OSM endpoint snapshot is © OpenStreetMap contributors under [ODbL](https://www.openstreetmap.org/copyright); see `data/sources/README.md` for the exact snapshot and query. Provider data are not granted a separate blanket software license.

These are model outputs, not journeys measured by drivers. ORS is a control without live traffic. A future agreement comparison cannot establish accuracy. Similar returned lengths do not establish identical route geometry. The source documents supplied by the team and local `.env` are excluded from this public repository.
