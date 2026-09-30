# Laya pattern classifier

Laya is an optional fourth evidence source in the investigation agent. Given a text summary of a flagged 20-report window it picks one of five pattern labels and reports a confidence. The agent can use that vote to lift or apply a confidence cap. It cannot choose a hypothesis. This document covers the classifier end to end: data, export, fine-tuning, the artifacts that exist, how it plugs into the agent, and what is unfinished.

Related: `docs/agent.md` (the state machine that consumes it), `docs/ml-pipeline.md` (injectors and the BiLSTM split it inherits).

## Three different things

The words "Laya data", "Laya model" and "the dataset" have been used interchangeably. They are not the same.

| | What it is | Where it lives | In git |
|---|---|---|---|
| Source data | AIS position reports in PostgreSQL. The export reads historical rows for 2026-04-01 to 2026-04-15. | `vessel_position` table | no |
| Derived experiment data | A class-balanced snapshot of injected window summaries: `train.jsonl` (7,500 rows), `holdout.jsonl` (1,500 rows), `manifest.json` | `data/laya/` | yes |
| Model artifacts | The fine-tuned Laya checkpoint: `model.safetensors`, `encoder/`, `tokenizer/`, `rl_agent_config.json`, plus a `benchmark_report.json` from the Kaggle run | `ml/laya_model/` | partly, see below |

`data/laya/*.jsonl` is not "the GHAST dataset". It is 9,000 rows derived from the source data by injecting synthetic spoofs, then summarizing. Neither the rows nor the model contain any real labeled spoofing event, because none exist in the source data.

Contents of `ml/laya_model/` in this checkout:

```text
benchmark_report.json        evaluation on data/laya/holdout.jsonl
rl_agent_config.json         training and calibration metadata
encoder/config.json          ModernBERT-large architecture config (no weights)
tokenizer/tokenizer.json, tokenizer_config.json
```

`model.safetensors` is not in the repo (`*.safetensors` is in `.gitignore`, added in the same commit as the results). Without it `laya.load()` cannot load this directory, and the scorer would log the failure and continue with the neutral stub. So a fresh clone cannot run the fine-tuned classifier until the weights file is copied in by hand. That file exists only wherever the Kaggle run's output was downloaded.

## What the classifier sees and returns

Input is one string built by `ml/features/summary.py::summarize_rows`, from rows with `received_at`, `latitude`, `longitude`, `sog_knots`, `cog_deg`. The same function builds the training text and the live text. Output of the summary is ten `key: value` lines:

```text
reports: 20, span_minutes: 1013.8
reported_speed_knots: mean 0.4, max 3.1
implied_speed_knots: mean 0.2, max 2.8
max_implied_over_max_reported: 0.9
max_step_km: 0.26
steps_claiming_speed_but_not_moving: 4, longest_run: 2
reports_repeating_earlier_positions: 10
max_course_change_between_reports_deg: 175
max_course_vs_travel_direction_deg: 25
implied_minus_reported_speed_trend_knots: +0.3
```

(That example is the first row of `train.jsonl`, a `freeze_replay` case.) Implied speed is recomputed from positions and timestamps, not read from the feature column, because the injectors leave that column stale.

The summary does not include the BiLSTM prediction error, which detectors voted, vessel class, position, time of day, or anything about the area. Laya sees only the 20-report track shape.

Five labels (`PATTERN_LABELS`): `normal_track`, `teleport_jump`, `gradual_drift`, `freeze_replay`, `impossible_kinematics`. The first is the injector's "no injection" control renamed, because Laya renders choice keys verbatim. The same question set (`pattern_questions()`, one `choice` question with a criterion sentence per label) is used for training rows and at inference.

In the live tool (`agent/tools/pattern_classifier.py`) the window is the 20 reports from `track_history` whose `received_at` is at or before the flagged report, newest 20. `track_history` returns all sources for the MMSI in a 48 hour span, so a window can in principle mix live and historical rows. Fewer than 20 reports means the tool returns `available: False`. The result carries the label, the confidence (`answer_confidence`, or the label's probability if that is missing) and the probability of every label.

The 20 reports the tool uses are not necessarily the window the BiLSTM scored: the scorer scores the newest 20 live reports, and the tool takes the 20 ending at the flagged report.

## Export

`ml/evaluation/laya_export.py`. Needs the database, not torch or a checkpoint.

```text
cd ml
python -m evaluation.laya_export --dsn $POSTGRES_DSN --source historical \
    --start 2026-04-01 --end 2026-04-15 --out-dir ../data/laya
```

Flags: `--per-class-train` (1500), `--per-class-holdout` (300), `--sample-permille` (50), `--max-windows`, `--seed` (0), `--keep-unobservable`.

Mechanics:

1. Streams clean 20-report windows with `stream_feature_windows`, 2,000 at a time.
2. `keep_window` keeps about 5% (permille 50) by hashing MMSI and window start.
3. `assign_case` hashes the window to one of the five labels, a severity from (0.25, 0.5, 0.75), and an injector seed. Labels come out nearly even, which avoids the injector's 25% control skew.
4. The split is by vessel using `is_validation_vessel`, the BiLSTM's holdout rule, so holdout vessels were not seen by the BiLSTM in training and no vessel is on both sides.
5. The label is injected (or the window left clean for `normal_track`), the result summarized, and a row written once the per-split, per-class quota is filled. It stops when every quota is met.

Row schema, matching Laya's own notebook: `id`, `split`, `mmsi`, `pattern`, `severity`, and `state`, `questions`, `gold`, each a JSON-encoded string. `gold` has probability 1.0 on the true label.

`manifest.json` records source, dates, seed, sampling, counts per split and class, and `quota_met`.

### The committed snapshot predates the observability gate

`data/laya/manifest.json` has no `observability_gate` key, and the gate commit (`b6a626f`) came after the snapshot commit (`c835f59`) and after the model results commit (`d503a39`). Code that runs with the gate on always writes that key, and with `--keep-unobservable` writes it as `null`. So the committed `train.jsonl`, `holdout.jsonl` and the model trained on them come from the ungated exporter.

The gate exists because two injectors do nothing visible on a vessel that is not moving. `freeze_replay` copies earlier positions forward, which on a stationary vessel is the same jitter that was already there. `impossible_kinematics` rotates one COG value, which is noise at rest. Rows labeled as those two patterns on stationary windows have summaries that look normal, so the label cannot be learned from the input and teaches the model to guess. The gate skips such rows and counts them in `manifest.json` under `observability_gate.skipped_unobservable`: a freeze replay must move some report at least 50 m, and an impossible-kinematics injection must land on a report with position-implied speed above 1 knot.

Consequences, all from the code and the recorded benchmark:

- The recorded benchmark below is from the ungated data. Its accuracy is not comparable to a gated run, because the unwinnable rows are gone from a gated set.
- A gated re-export needs more windows read to fill the quota, so expect to raise `--sample-permille` or widen the dates. The unit tests for the gate use synthetic windows (`test_laya_export.py`). It has not been run against the database.
- With the gate on, the model learns that a stationary-looking window is `normal_track`. It will therefore not flag a freeze or a course flip on a moored vessel. The summary cannot show one.
- Even ungated, the holdout confusion in the benchmark is concentrated on exactly the three classes whose evidence disappears at rest (below), which is consistent with the gate's rationale. No stationary versus underway breakdown of the holdout has been computed, so the reason is inferred.

## Fine-tuning

The base model is `convaiinnovations/laya` (ModernBERT-large encoder, about 421M parameters per Laya's notebook text). Fine-tuning has to run on a GPU host; the recorded run used Kaggle.

What is committed as `notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb` is Laya's upstream notebook, unchanged: it still loads `LocalLLaMA/typed-decisions` for training and for its test split. It is not the notebook that produced GHAST's model. The run that did was adapted elsewhere to read `train.jsonl` and `holdout.jsonl`; that adapted notebook is not in the repo. `rl_agent_config.json` from the run records 7,313 updates, 1 epoch, 1.96 hours, `world_size: 1`, `bf16`, `fine_tuned_from_checkpoint: true`. The benchmark report calls it a 2xT4 run while `world_size` says 1; this is unexplained in the repo.

The steps that were followed (from the previous version of this document, still the only procedure on record): upload the two jsonl files as a Kaggle dataset, replace the two `load_dataset("LocalLLaMA/typed-decisions", ...)` calls with `load_dataset("json", data_files=...)`, keep the row schema, and download the notebook's output directory.

## Recorded evaluation

Source: `ml/laya_model/benchmark_report.json`, produced by that Kaggle run, over all 1,500 rows of `data/laya/holdout.jsonl`. Not reproducible from the repo (no weights, no adapted notebook).

| Metric | Value |
|---|---|
| accuracy | 0.8293 (chance 0.20) |
| soft accuracy / Brier / KL / total variation | 0.7839 / 0.2141 / 0.3764 / 0.2161 |
| expected calibration error | 0.0157 |
| latency p50 / p95 | 36.4 / 39.5 ms (device not recorded) |
| true `normal_track` called a spoof | 0.2933 |
| true spoof called `normal_track` | 0.0983 |

Per-class accuracy: `normal_track` 0.7067, `teleport_jump` 1.0, `gradual_drift` 0.9967, `freeze_replay` 0.7067, `impossible_kinematics` 0.7367.

The confusion matrix in the report is keyed `[predicted][true]`, the opposite of the usual reading. Checked against the counts: each column sums to 300 and each row does not, and the reported rates match this orientation (true normal called a spoof is 1+3+57+27 = 88 of 300; true spoofs called normal is 68+50 = 118 of 1200). Read that way: the errors are between `normal_track`, `freeze_replay` and `impossible_kinematics`; `teleport_jump` and `gradual_drift` are almost never confused with anything.

Accuracy by minimum confidence, over all classes:

| min confidence | share of rows at or above | accuracy on those rows |
|---|---|---|
| 0.5 | 0.807 | 0.917 |
| 0.6 | 0.722 | 0.958 |
| 0.7 | 0.688 | 0.972 |
| 0.8 | 0.659 | 0.985 |
| 0.9 | 0.628 | 0.993 |

What this does and does not tell you:

- It is accuracy on a balanced, synthetic, same-generator holdout. It says the model recovers the injector's labels from the summary. It does not say anything about real spoofing.
- The agent uses the tool asymmetrically (see below): what matters is how often a confident non-normal answer is right when the true class is normal, and how often a confident `normal_track` answer is right when the true class is a spoof. Neither is in the report. The 0.7 row above is accuracy over all five classes and mixes both.
- In production the model sees flagged windows only, a population selected by the detectors. The holdout is 20% `normal_track` by construction. The class balance in live flagged windows is unknown, so the holdout numbers do not transfer as rates.

## How the agent uses it

Constants and logic are in `agent/orchestrator/state_machine.py`.

- `PATTERN_MIN_CONFIDENCE = 0.7`. Uncalibrated placeholder. The benchmark above exists but the constant has not been reviewed against it, so treat 0.7 as still unset. The temperature values in `rl_agent_config.json` (1.6255, 1.2, 1.2) are Laya's own calibration for its three question types, not something GHAST tuned.
- `_pattern_vote(evidence)` returns `"agrees"` if the tool was available, confidence is present and at least 0.7, and the label is anything other than `normal_track`; `"contradicts"` if it is `normal_track` at that confidence; otherwise `None` (unavailable, low confidence, or no tool).
- The vote is used in exactly one place, `_apply_single_detector_cap`:

| Situation | Effect |
|---|---|
| confident `normal_track`, hypothesis is not `jamming`, `benign`, `unresolved` | confidence capped at 0.5, even with several detector votes |
| confident non-normal label and exactly one detector vote | cap not applied |
| any other case | unchanged |

- Exceptions: `jamming`, `benign` and `unresolved` are never touched. The sequence-corroborated `freeze_replay` tier ignores a `normal_track` contradiction (`ignore_contradiction=True`) but the single-detector cap still applies to it unless Laya agrees.

What it is not allowed to do, and does not: choose the hypothesis, produce `benign`, override a jamming zone match, raise a confidence, or affect a flag whose score is below the BiLSTM threshold. It can only remove a cap (`0.5` to the tier's own value) or apply one.

Points that follow from the code and are easy to miss:

- "Agrees" means any non-normal label. It does not need to match the hypothesis or the detector that fired. Laya answering `teleport_jump` lifts the cap on a `freeze_replay` hypothesis just as `freeze_replay` would.
- A lifted cap only changes a result that would otherwise be reported. A `targeted_spoof` (0.72) or a corroborated `freeze_replay` (0.8) with a single detector vote goes from escalated to reported. `equipment_fault` (0.55) is below the report threshold of 0.7 whatever Laya says.
- With the shipped `--min-votes 2` container default, a flag has at least two detector votes, so the "agrees" branch never has a cap to lift. In that configuration Laya can only lower confidence (via a confident `normal_track`), never raise it.
- Failure never stops an investigation: no model, no `laya` package, too little history, or an exception all return `available: False` and the vote is `None`. The result is always written to `evidence["pattern_classifier"]` and the tool call log, including when it is the neutral stub, so every stored incident shows whether Laya voted.

## Enabling it

Requirements, all needed together:

1. A model directory with `model.safetensors`, `encoder/`, `tokenizer/`, `rl_agent_config.json` (the committed files plus the weights).
2. Docker: `WITH_LAYA=1` and `GHAST_LAYA_MODEL=/laya_model` in `infra/docker/.env`, then rebuild the `scoring` image. Compose already mounts `ml/laya_model` at `/laya_model`.
3. Without Docker: `pip install laya` and `--laya-model <dir>` (or `GHAST_LAYA_MODEL`) on `scoring/live_scorer.py`.

`scoring/requirements-laya.txt` is just `laya`, unpinned. It brings in transformers and torch dependencies. Inference runs in a worker thread with the scorer's device (`cpu` unless `--device cuda`). If the load fails, the scorer logs the exception and runs with the stub.

## Status and known issues

| Piece | State |
|---|---|
| shared summary and question set | implemented, tested |
| exporter (with the observability gate) | implemented, unit tested on synthetic windows; gate never run against the database |
| `data/laya/` snapshot | produced without the gate |
| fine-tuned model | trained once, on the ungated snapshot; weights not in the repo |
| holdout evaluation | recorded once (`benchmark_report.json`); synthetic same-generator data only |
| `pattern_classifier` tool, `investigate` and `form_hypothesis` wiring | implemented, tested with fakes |
| `load_laya_predictor` against the real library | not tested |
| `PATTERN_MIN_CONFIDENCE` calibration | not done |
| gated re-export, retrain, re-evaluation | not done |
| evaluation on real incidents or real spoofing | not possible yet, no labeled data |
| effect on real incidents | unmeasured; `ghast_latest_report.md` shows no classifier vote in its evidence summary |

Known limitations:

- Synthetic to real gap. The model learns the injector's version of each pattern. The injector's `freeze_replay` loops earlier positions forward; it does not usually hold one position still, while the agent's freeze corroboration and `freeze_replay_detector` look for held positions. Real spoofing may resemble neither.
- The injection changes positions or COG only. Other real anomalies (timestamp problems, duplicate messages, AIS sentinel values) are not represented, so their summaries are out of distribution for the classifier and the answer is undefined.
- The summary carries no vessel type or context. A fishing vessel manoeuvring and a spoofed track can look the same to it.
- Laya's own README lists negation and label-wording weaknesses, and its calibration slice comes from its training items.
- The classifier is trained on data produced by the same injectors used to evaluate the BiLSTM, so agreement between the two is not independent evidence about real spoofing.
