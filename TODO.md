# Remaining review and future-data work

Google Demo with TRAFFIC_AWARE_OPTIMAL now works, the separate Google-ORS pilot is complete, the cloud worker is active, and the ZIP is on Drive. No payment or billing upgrade was made.

1. Review the current report and its limitations before submission. The original TomTom-2GIS comparison was not performed. The user-approved current design compares Google with traffic to an ORS control without live traffic. It does not measure accuracy or isolate a causal traffic effect.
2. Submission is due **25 October at 18:00 Astana time**. The amended windows are: 24 October peak 17:30–19:30 and off-peak 22:00–24:00; 25 October off-peak 10:00–12:00 and a short peak window 17:30–17:40. Each permits at most twenty paired requests, eighty in total. Cloud collection stops by 25 October at 17:40; it may stop earlier on a quota or access error. Prepare a submission-ready version from the first three windows in advance, and include the final evening batch only if it actually completes in time. Missing windows cannot be recreated later. Preserve the two-date limitation.
3. After the relevant windows, retrieve and describe the actual aggregate results. Do not reconstruct individual Google estimates or run the original route/day Wilcoxon from window aggregates. The scripts explicitly keep inference disabled.

```powershell
cd C:\rmt3
git pull --ff-only
.\.venv\Scripts\python.exe analysis/process.py
.\.venv\Scripts\python.exe analysis/figures.py
.\.venv\Scripts\python.exe analysis/google_control_figure.py
.\.venv\Scripts\python.exe analysis/google_followup_summary.py
.\.venv\Scripts\python.exe report/build_report.py
```

These commands create a newer report from available data. For the exact delivered main sample, follow `docs/reproduce_report.md`. Review the rebuilt DOCX layout and figures before submission. The current report and original morning version remain documented by their snapshot files.

4. The Drive archive is already uploaded and publicly readable at https://drive.google.com/file/d/1qFbs-NPxApQqgvHte2qbVkNL96yaXeB4/view . It is a delivery snapshot and does not update itself whenever new API data arrives. Replace it with a newly reviewed archive after a future report revision.
5. Only if returning to the original TomTom/2GIS design: obtain the applicable comparison, retention and publication rights and record a new amendment before collecting. HERE still has no key; the standard Google key still has 403, but the distinct Demo Key is the active source for the bounded tests.

Never upload `.env`, API keys or the virtual environment. Preserve the initial expected-results files and frozen samples. The original ER8 coverage threshold was missed by sparse cron delivery; extra exploratory requests must not be used to disguise this gap.
