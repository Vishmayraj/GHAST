# Threshold recalibration: what the two real-traffic reports say

Source files: `ml/reports/historical_epoch_010.json` (2026-04-01 to 2026-04-16, validation vessel split) and `ml/reports/live_epoch_010.json` (2026-09-27 to 2026-10-02, same split). Both score `checkpoints/epoch_010.pt` at `OPERATING_THRESHOLD = 0.004946`. Errors are in degrees of lat/lon delta.

| | historical | live |
|---|---|---|
| windows | 319,298 | 35,176 |
| reports | 6,066,662 | 668,344 |
| flag rate at 0.004946 | 33.4% | 39.3% |
| underway flag rate | 57.6% | 60.8% |
| stationary flag rate | 22.9% | 20.9% |
| p50 error | 0.0042 | 0.0038 |
| p95 error | 0.0170 | 0.2044 |
| p99 error | 0.0343 | 0.8688 |
| p99.9 error | 0.1205 | 3.7942 |
| threshold for 5% flag rate | 0.0170 | 0.2044 |
| threshold for 1% flag rate | 0.0343 | 0.8688 |
| threshold for 0.1% flag rate | 0.1205 | 3.7942 |
| any detector flags | 34.1% | 40.5% |
| two or more detectors flag | 0.30% | 0.75% |

## Reading it

The placeholder threshold flags a third of all reports on both sources, so it cannot be an alert budget. It is below the historical p50 plus noise: the median error is 0.0042 and the threshold is 0.0049. The underway versus stationary split is the same shape on both sources, with underway reports flagged about 2.5 to 3 times as often, which fits the model being trained on a feature set with no time-step input (`docs/ml-pipeline.md`).

The live tail is not comparable to the historical tail. Live p99 is 0.87 degrees and p99.9 is 3.8 degrees (about 420 km), against 0.034 and 0.12 historically. The median is nearly identical, so the bulk of live traffic looks like the historical bulk and the difference sits in the tail. `ml/features/quality.py` attributes that tail to unscorable transitions (AIS sentinel coordinates, duplicate timestamps, long silences, antimeridian steps). The live JSON has no field for reports left out by that gate, so it was produced before `score_checkpoint` applied the gate (commit `171ee06`). Do not pick a live threshold from the live column above.

## What is not decided

- The alert budget (flags per 1,000 reports) is the owner's call. Nothing was chosen here, so `OPERATING_THRESHOLD` is unchanged.
- The live report has to be re-run with the quality gate (needs Postgres and the checkpoint, not run in the session that wrote this note). Only then is the live column usable.
- Since 2026-10-01 the scorer reads its alert thresholds from `threshold_config` (agent `threshold_agent`), so `OPERATING_THRESHOLD` is now the fallback and the vote threshold, not the whole story. Which live number the `threshold_agent` should start from is the same alert-budget question.
