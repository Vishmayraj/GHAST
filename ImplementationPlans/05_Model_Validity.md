# 05 Model validity: ablations and a v2 detector

Depends on: nothing hard. Plan 01 reviews make the results sharper; the label-free checks below stand alone.

## Why

`docs/ml-pipeline.md` lists design problems nobody has tested: the BiLSTM is bidirectional and gets implied speed as an input, so "next step prediction" can see the step it predicts; there is no time-step input, so error scale depends on reporting interval; the historical model never saw `rate_of_turn`; sentinels (511, 360, 102.3) are treated as real numbers; no feature scaling. None of that needs labels to investigate.

## Label-free evaluation criteria

Real windows have no ground truth, so judge variants on:

1. Fair held-out loss: next-step error on validation vessels with a causal model (no future rows visible).
2. Interval invariance: correlation between per-report prediction error and elapsed time since the previous report, and the flag-rate ratio across reporting-interval buckets. A good detector's flag rate should not track reporting interval.
3. Source stability: ratio of flag rates, historical versus live, at one threshold. Closer to 1 is better.
4. Stationary versus underway flag rates (`score_checkpoint` already reports the split).
5. With plan 01 reviews: precision at a fixed alert budget on reviewed incidents. Use it as the tiebreaker when it exists.

## Experiments (one training run each, small enough for a laptop or one GPU)

Add `ml/experiments/` with a config file per variant and one runner that trains and writes a `score_checkpoint`-style JSON. Train on real windows only. Log to MLflow (also closes the HLD's experiment-tracking item; `training` does not use it today).

1. Baseline: current architecture, retrained, to confirm the pipeline reproduces `epoch_010` behavior.
2. Causal: unidirectional LSTM.
3. No leak: drop implied speed of the target step (or compute features only from rows up to `t`).
4. Time-aware: add elapsed seconds as an input and predict delta per unit time (or scale the target by it).
5. Live-inclusive: train on live rows so `rate_of_turn` and its mask vary; compare with historical-only.
6. Clean inputs: map sentinels to missing with masks for SOG and COG, add feature scaling.
7. Combination of the winners.

## Deliverable

`docs/research_notes/model-v2-ablations.md`: a table of variants against the five criteria, the chosen v2, and what was not tested. If v2 wins, train it with provenance recorded (hyperparameters, commit, feature version) in the checkpoint (extend `run_training`), calibrate its threshold with `score_checkpoint` (plan 01 process), and swap the scorer's checkpoint through configuration instead of the hardcoded compose path.

Only after this: decide whether the HLD's transformer migration is worth doing, and say why in that note.

## Done when

- Every variant has a JSON report in `ml/reports/` and the note compares them.
- `docs/ml-pipeline.md` "known limitations" is updated to what was actually tested.
- No claim of accuracy appears anywhere without reviewed-incident data behind it.
