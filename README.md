# Astana route estimate study

Group SE-2424. Akhanov Yerdaulet, Shadiyarbek Syrym, Sabyrzhan Akylbek.

This repository contains RMT Assignment 3 evidence and an explicitly amended **Google traffic-aware versus openrouteservice (ORS) control** design. A new Google Demo Key works without billing. The initial TomTom–2GIS design remains unperformed and its original expectations are preserved; its access and publication constraints are documented in `docs/api_check.md`. The standard Google key still returned 403, but the separate Demo Key returned 200 with `TRAFFIC_AWARE_OPTIMAL`.

Twenty directed origin-destination pairs were selected from public OpenStreetMap data. The original ORS pilot completed three full cycles. Since the original cron delivered too few events, a bounded GitHub worker now samples four rotating ORS routes no more often than every fifteen minutes and hands off to a successor. It also runs one Google–ORS batch of twenty routes in each of five prospective peak/off-peak windows defined in `docs/google_followup_protocol.json`. All collection stops at the original fixed end, 26 October 2026 at 07:32 Astana time, or earlier on exhausted budgets or relevant errors. Missing observations are never backfilled.

Google route estimates are processed only in memory. The public outputs contain original aggregate comparison statistics, request statuses/timestamps and permitted ORS raw evidence. The user explicitly approved this retention tradeoff. Individual Google times, distances and geometry are not archived; the Google aggregates therefore cannot be independently recalculated from saved raw responses. The published code and protocol support a new prospective replication, with new live values. Aggregate windows do not recover the original route-by-day inferential dataset.

## Reproduce

The updated report uses a frozen sample of 68 ORS observations: 60 local pilot requests, 4 from a manually triggered GitHub Actions verification run and 4 from an actual `schedule` event. Trigger metadata is retained in `docs/cloud_runs.json`. The initial morning sample and report are preserved separately. To reproduce the delivered sample exactly, follow [the frozen-snapshot instructions](docs/reproduce_report.md). The commands below instead analyze all observations currently present in the live data folder.

The frozen CSV is stored without Git newline conversion, so its SHA-256 matches its manifest on every platform. Other input hashes in the report snapshot describe the Windows workspace bytes at build time; Git or another operating system may change text line endings without changing data values. The preregistration checker compares normalized line endings explicitly.

Use Python 3.11 or newer. On Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe analysis/process.py
.\.venv\Scripts\python.exe analysis/figures.py
.\.venv\Scripts\python.exe analysis/google_control_figure.py
.\.venv\Scripts\python.exe analysis/google_followup_summary.py
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
- `.github/workflows/collect_resilient.yml`: active bounded worker with automatic handoff and secrets supplied by GitHub. The original `collect.yml` cron workflow is disabled after sparse event delivery.

## Data attribution and interpretation

ORS results: **© openrouteservice by HeiGIT | Data from OpenStreetMap**, licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). See [ORS terms](https://account.heigit.org/info/tos). The OSM endpoint snapshot is © OpenStreetMap contributors under [ODbL](https://www.openstreetmap.org/copyright); see `data/sources/README.md` for the exact snapshot and query. Provider data are not granted a separate blanket software license.

These are model outputs, not journeys measured by drivers. ORS is a control without live traffic. A future agreement comparison cannot establish accuracy. Similar returned lengths do not establish identical route geometry. The source documents supplied by the team and local `.env` are excluded from this public repository.
