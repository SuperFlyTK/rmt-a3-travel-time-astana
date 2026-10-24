# Expected results fixed before instrument pilot

Fixed: 24.10.2026; UTC 2026-10-24T02:32:16.123968+00:00.

O1-O5 are carried forward explicitly from A1. A2 has no separate numbered objectives list.

No primary-provider comparison has been collected. Earlier credential checks are excluded from research data. Numerical thresholds below are prospective design choices, not observations.

| No. | Expected result | Connected objective | Falsification | Confidence and why | Scope |
|---|---|---|---|---|---|
| ER1 | The peak to off-peak ratio of median absolute TomTom-2GIS difference is at least 1.5. | O4 Compare periods | Ratio below 1.5 after both periods exist | Low; directional H1 has no local pilot evidence | primary |
| ER2 | At least 70% of eligible primary pairs have lengths within 5% of their pair mean. | O3 Quantify disagreement | Proportion below 70% | Low; endpoint equality need not produce the same path | primary |
| ER3 | Median signed difference TomTom minus 2GIS is positive in the completed sample. | O3 Quantify disagreement | Median at most zero | Low; exploratory directional expectation, not prior fact | primary |
| ER4 | The peak IQR of signed differences exceeds the off-peak IQR. | O5 Describe route and time variation | Peak IQR at most the off-peak IQR | Low; hypothesis about variation is unverified | primary |
| ER5 | Fewer than 2% of ORS instrument requests fail to return a valid route. | O2 Collect auditable observations | Failure proportion at least 2% | Medium; one previous technical check succeeded | instrument |
| ER6 | At least 95% of ORS instrument requests finish within 10 seconds. | O2 Collect auditable observations | Proportion below 95% | Medium; network and service delays can defeat it | instrument |
| ER7 | All 20 routes produce at least one valid ORS result during the three pilot cycles. | O1 Fix routes and periods | Fewer than 20 routes have any valid pilot observation | Medium; public road endpoints still require routing verification | instrument |
| ER8 | At least 90% of the 768 nominal ORS scheduled requests are recorded in the 48-hour window. | O2 Collect auditable observations | Fewer than 692 attempts after the window ends | Medium; Actions scheduling may delay or skip runs | instrument |

- bad: Traffic-provider permissions remain unavailable; only control-instrument evidence exists and H1 cannot be tested.
- bad: At least 2% of ORS requests fail or fewer than 20 routes work; revise the instrument in a dated amendment.
- medium: All routes work but fewer than 90% of scheduled attempts are recorded; retain actual times and describe incomplete coverage.
- medium: Primary collection later becomes permitted but period contrast is below 1.5; report evidence against ER1 without editing it.
- good: The ORS pilot covers all routes and fewer than 2% of requests fail; the data pipeline is ready for a permitted primary comparison.
- good: A later permitted primary sample spans both periods and reaches the ER1 threshold with interpretable route-aware uncertainty.
