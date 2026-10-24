# What remains for the team

The delivered pilot demonstrates the instrument using ORS only. It is not a completed traffic-aware provider comparison.

1. Review the report, actual timestamps, findings, source references and course requirements before submitting it. The original RQ is still prospective.
2. Obtain written TomTom permission for comparison with competitors and the required storage/publication rights, or choose another provider that permits this study. Current terms: https://docs.tomtom.com/legal/terms-and-conditions (clauses 11.4, 11.6.1, 11.6.10).
3. Confirm the applicable Kazakhstan 2GIS demo licence permits retaining and publicly publishing routing measurements and geometry. A successful API call alone is not this permission. No unsupported rights are asserted.
4. Google Cloud still requires a prepayment and the last check returned 403. No payment or upgrade was made. HERE requires billing details and has no key. These actions were skipped.
5. If a traffic-aware provider becomes usable and its data use is permitted, make a dated protocol amendment before enabling it. Recheck quotas and run one credential check. Add the matching GitHub Secret to the workflow's environment mapping as well as approving the provider in the configuration: the current workflow injects only ORS_KEY. Do not silently relabel the ORS control as traffic-aware or edit pre-registered expectations.
6. Upload `RMT_A3_drive_mirror.zip` to the prepared Drive folder. Check repository, report links and Drive from a private browser window. The folder must remain accessible through exams.
7. After collection, pull remote observations and rebuild locally:

```powershell
cd C:\rmt3
git pull --ff-only
.\.venv\Scripts\python.exe analysis/process.py
.\.venv\Scripts\python.exe analysis/figures.py
.\.venv\Scripts\python.exe report/build_report.py
```

Only test peak versus off-peak after a permitted primary sample contains the required periods and independent route/day coverage. The code deliberately suppresses pilot inference. If remaining exclusively ORS, interpret only instrument and control observations.

Never put `.env`, secret values or the virtual environment in the Drive archive. The scheduled collection has a fixed forty-eight-hour end and request budget; it does not run forever.
