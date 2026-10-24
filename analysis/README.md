# Reproduce the analysis

From the repository root, with the project dependencies installed:

```powershell
python analysis/process.py
python analysis/figures.py
python -m unittest discover -s tests -p test_analysis.py -v
```

The processing script reads only `data/raw/*.csv`. It never changes a raw file.
Input SHA256 hashes, the processing script hash, Python version, timezone version,
analysis rules and generation time are saved in `results/version_manifest.json`.
Tests use explicit synthetic fixtures in temporary directories. They do not write
observations into the research dataset.

## Observation rules

Every raw row is retained in `results/observations.csv`, with its original source
filename and line number. An exact duplicate is excluded after its first occurrence.
Conflicting rows sharing phase, cycle, route and provider are all excluded from
pairing. Reused nonempty request identifiers are also excluded. No retry is chosen
because its value is more convenient. Failures and invalid rows remain visible.

A usable response requires status `ok`, HTTP 200, timezone-aware consistent timestamps,
positive finite duration and distance, and implied speed from 2 through 200 km/h.
An invalid optional no-traffic duration is logged but does not invalidate the primary
traffic-duration estimate, because that optional field is not used in agreement.
Empty optional fields and empty error fields are counted in the data dictionary.
Missingness is measured separately for raw observations, audited observations and pairs.

Period classification uses `Asia/Almaty`. Peak is [17:30, 19:30). Off-peak is
[10:00, 12:00) or [22:00, 24:00). All other times are `other`. Endpoints are excluded.
A provider pair must share phase, cycle and origin–destination route. Its request
starts must be no more than 60 seconds apart. Pairs crossing a period or local-date
boundary are excluded. The pair timestamp is the midpoint of the two request starts.

## Agreement quantities

Provider order is TomTom, 2GIS, ORS. TomTom−2GIS is the primary traffic-aware
comparison. Any pair containing ORS is a control comparison. ORS is not labelled
traffic-aware. Without valid primary pairs, the primary comparison and inferential
outputs are empty or explicitly `not_run`; control observations cannot substitute
for evidence of traffic-aware disagreement.

For duration estimates A and B, `d=A−B`, `m=(A+B)/2`, absolute disagreement is
`|d|`, signed relative disagreement is `100d/m`, and pair sMAPE is `100|d|/m`.
MAE is the mean `|d|`; RMSE is the square root of mean `d²`. Smaller MAE, RMSE,
sMAPE and median absolute disagreement indicate closer provider agreement. They
do not measure accuracy because no observed journey-time ground truth is available.
Quartiles use linear interpolation at position `(n−1)p` (type-7 quantiles).
IQR is the third quartile minus the first quartile.

Length disagreement is `100|LA−LB|/((LA+LB)/2)`. Length-comparable pairs have
disagreement at most 5%. This is a length proxy; it does not establish identical
street geometry. All, comparable and noncomparable subsets are reported separately.

## Repeated observations and uncertainty

Pilot results are descriptive. No pilot p-value or confidence interval is generated.
For nonpilot data, the percentile bootstrap resamples entire routes, retaining all
their cycles and days. It uses seed 2424 and 2,000 replicates by default. A 95%
interval is withheld below eight routes or two local dates. The two-day guard is a
minimum, not evidence that temporal or spatial dependence is absent. Intervals are
conditional on the chosen routes and observation dates, and should not be treated
as population estimates for every Astana road.

For a later peak/off-peak comparison, the script first computes median absolute
disagreement within each route×day×period, then peak minus off-peak for matched
route-days. It takes each route's median contrast over dates, so repeated cycles
and days do not masquerade as independent routes. With at least eight matched
routes and two dates, it computes a two-sided exact sign-permutation signed-rank
test across route contrasts. Zero contrasts are omitted from the test; tied absolute
values receive average ranks. The rank-biserial effect is `(W+−W−)/(W++W−)`.
Symmetric route contrasts and independent route clusters remain assumptions.
All lengths is the primary subset; length-stratified tests are exploratory and
are not adjusted for multiple comparisons. Pilot rows never enter these tests.

## Figures

Figures are rendered only from processed CSV records. With an ORS-only pilot,
request outcomes, period coverage, route durations, distance versus duration,
request latency and a verbatim extract provide instrument evidence. Empty primary
comparisons are marked skipped in `figures/analysis_manifest.json`. No invented
observations or decorative placeholder results are used. The manifest records
captions, exact input files, their hashes and the plotting-library version.
