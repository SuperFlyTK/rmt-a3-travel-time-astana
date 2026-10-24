# Reproduce the report snapshot

Scheduled collection can append to the live daily CSV after this report is written. The frozen CSV in `data/report_snapshots/2026-10-24-afternoon/raw` preserves the exact submitted sample. Its manifest gives the time and SHA256. Original per-request response and geometry files have unique identifiers and remain under `data/raw/`.

From the repository root, with dependencies installed, run:

```powershell
.\.venv\Scripts\python.exe analysis/process.py --raw-dir data/report_snapshots/2026-10-24-afternoon/raw
.\.venv\Scripts\python.exe analysis/figures.py
.\.venv\Scripts\python.exe scripts/route_map.py --cycle pilot-1
.\.venv\Scripts\python.exe analysis/google_control_figure.py
.\.venv\Scripts\python.exe analysis/google_followup_summary.py
.\.venv\Scripts\python.exe report/build_report.py
```

The observed counts, statistics and figure data reproduce from the frozen CSV. Generated-at timestamps and DOCX ZIP metadata may differ; byte-for-byte DOCX identity is not promised. To create an updated report from newly collected observations, omit `--raw-dir` and label the resulting report with its new analysis timestamp. Never overwrite the frozen snapshot or the original expected-results files.

The original morning sample of 64 records remains in `data/report_snapshot/raw`; its report and metadata are preserved in `report/versions/2026-10-24-morning`. The current report uses the separately frozen afternoon sample.

The separate Google-ORS control figure and table read `results/google_control_benchmark.csv`. This is the saved output of a transient comparative test: individual Google estimates were not retained. Its aggregate values cannot be independently recalculated from archived Google responses. The protocol, code, ORS observations and request status/timestamps make the method repeatable, but a new API test observes new live values. Original and follow-up protocol commits precede their respective research requests.
