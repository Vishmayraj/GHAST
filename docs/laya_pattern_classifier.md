# Laya pattern classifier

Laya is an optional fourth evidence source in the investigation agent. Given a text summary of a flagged 20-report window it picks one of five pattern labels and reports a confidence. The agent can use that vote to lift or apply a confidence cap. It cannot choose a hypothesis. This document covers the classifier end to end: data, export, fine-tuning, the artifacts that exist, how it plugs into the agent, and what is unfinished.

Related: `docs/agent.md` (the state machine that consumes it), `docs/ml-pipeline.md` (the BiLSTM vessel split it inherits).

## Three different things

The words "Laya data", "Laya model" and "the dataset" have been used interchangeably. They are not the same.

| | What it is | Where it lives | In git |
|---|---|---|---|
| Source data | Real AIS position reports in PostgreSQL. The export samples real windows from live or historical rows. | `vessel_position` table | no |
| Derived experiment data | A review queue of real window summaries, and once a person has labeled it, `train.jsonl`, `holdout.jsonl`, `manifest.json`. None exist yet. | `data/laya/` | no |
| Model artifacts | The fine-tuned Laya checkpoint: `model.safetensors`, `encoder/`, `tokenizer/`, `rl_agent_config.json`, plus a `benchmark_report.json` from the Kaggle run | `ml/laya_model/` | partly, see below |

`data/laya/*.jsonl` will not be "the GHAST dataset". It will be real window summaries with human labels. **The model currently in use was fine-tuned on a different set: 9,000 rows made by injecting synthetic spoofs into real windows.** That injector and snapshot have been removed from the repo (recoverable from commit `c835f59`). So the current model has never seen a real labeled spoofing event, and nothing in the repo can currently reproduce or replace its training data until real labels exist.

Contents of `ml/laya_model/` in this checkout:

```text
benchmark_report.json        evaluation on the removed injected holdout
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

(That example is a `freeze_replay` case from the removed injected snapshot, kept to show the format.) Implied speed is recomputed from positions and timestamps, not read from a cached feature column, because `track_history` rows have no such column.

The summary does not include the BiLSTM prediction error, which detectors voted, vessel class, position, time of day, or anything about the area. Laya sees only the 20-report track shape.

Five labels (`PATTERN_LABELS`): `normal_track`, `teleport_jump`, `gradual_drift`, `freeze_replay`, `impossible_kinematics`. The first means "nothing wrong" and is named for what it means, because Laya renders choice keys verbatim. The same question set (`pattern_questions()`, one `choice` question with a criterion sentence per label) is used for training rows and at inference.

In the live tool (`agent/tools/pattern_classifier.py`) the window is the 20 reports from `track_history` whose `received_at` is at or before the flagged report, newest 20. `track_history` returns all sources for the MMSI in a 48 hour span, so a window can in principle mix live and historical rows. Fewer than 20 reports means the tool returns `available: False`. The result carries the label, the confidence (`answer_confidence`, or the label's probability if that is missing) and the probability of every label.

The 20 reports the tool uses are not necessarily the window the BiLSTM scored: the scorer scores the newest 20 live reports, and the tool takes the 20 ending at the flagged report.

## Export

`ml/evaluation/laya_export.py`. Needs the database for `queue`, nothing for `build`. Neither needs torch or a checkpoint. No windows are altered: every summary describes a track that was really received.

Real windows have no labels, so the data is made in three steps with a person in the middle.

```text
cd ml
# 1. sample real windows into a review queue (no answers in it)
python -m evaluation.laya_export queue --dsn $POSTGRES_DSN --source live \
    --start 2026-09-01 --end 2026-09-30 --out ../data/laya/review_queue.jsonl

# 2. a reviewer writes labels.jsonl: {"id": "<queue id>", "label": "<one of the five labels>"} per line

# 3. join queue and labels into the files Laya's notebook reads
python -m evaluation.laya_export build --queue ../data/laya/review_queue.jsonl \
    --labels ../data/laya/labels.jsonl --out-dir ../data/laya
```

`queue` flags: `--source`, `--start`, `--end`, `--out`, `--sample-permille` (5), `--max-rows` (2000), `--max-windows`, `--seed`. It streams windows with `stream_feature_windows`, keeps a hash-thinned sample (by MMSI and window start, so streaming order does not bias which vessels appear), and writes one row per window: `id` (`<mmsi>-<window start>`), `split`, `mmsi`, `source`, `window_start`, `window_end`, `state` (the `features.summary` text, the same text the live tool sends) and `questions`.

The split is by vessel using `is_validation_vessel`, the BiLSTM's holdout rule. `build` refuses to write if any vessel is in both splits.

`build` validates the labels (each must be one of `PATTERN_LABELS`, no conflicting duplicates), ignores unlabeled queue rows, writes `train.jsonl` and `holdout.jsonl` in the eight-column schema Laya's notebook loads (`id`, `split`, `mmsi`, `pattern`, `severity`, `state`, `questions`, `gold`; `severity` is always null now, and `gold` has probability 1.0 on the human label), and writes `manifest.json` with counts per split and class, how many queue rows were unlabeled, label ids not found in the queue, and which split-and-class pairs are under 50 labels.

A random sample of real windows is almost all `normal_track`, so a random queue is a wasteful thing to label. Sampling flagged windows (from the `incidents` table and from detector votes) is part of `ImplementationPlans/04_Laya_Real_Labels.md`. Until then, `queue` is the plain version.

## Fine-tuning

The base model is `convaiinnovations/laya` (ModernBERT-large encoder, about 421M parameters per Laya's notebook text). Fine-tuning has to run on a GPU host; the recorded run used Kaggle.

`notebooks/laya_finetune_ghast_kaggle_2xT4.ipynb` is the GHAST-adapted notebook (committed 2026-09-30, replacing an earlier copy of Laya's upstream notebook). It clones `Vishmayraj/GHAST`, reads `data/laya/train.jsonl` and `holdout.jsonl` through an explicit eight-column schema, trains with Laya's RLCD method unchanged, evaluates on the holdout with a GHAST report (per-class accuracy, confusion matrix, false positive rate, accuracy by confidence, stationary versus moving split), and exports `model.safetensors`, `rl_agent_config.json`, `encoder/` and `tokenizer/`. It does not push to Hugging Face. **It cannot run until `data/laya/train.jsonl` and `holdout.jsonl` exist again**, which now means building them from labeled real windows. `rl_agent_config.json` from the earlier run from the run records 7,313 updates, 1 epoch, 1.96 hours, `world_size: 1`, `bf16`, `fine_tuned_from_checkpoint: true`. The benchmark report calls it a 2xT4 run while `world_size` says 1; this is unexplained in the repo.

To run it: build `train.jsonl` and `holdout.jsonl` (above), commit or attach them so the notebook can find `data/laya/`, run the notebook on a Kaggle GPU, and copy the exported directory into `ml/laya_model/`.

## Recorded evaluation

Source: `ml/laya_model/benchmark_report.json`, produced by the earlier Kaggle run over all 1,500 rows of the removed injected `holdout.jsonl`. **This benchmark describes the model currently in use, and it is a benchmark on injected data.** Not reproducible from the repo (no weights, and the holdout file is gone).

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

- It is accuracy on a balanced, injected, same-generator holdout. It says the model recovered the injector's labels from the summary. It does not say anything about real spoofing.
- The agent uses the tool asymmetrically (see below): what matters is how often a confident non-normal answer is right when the true class is normal, and how often a confident `normal_track` answer is right when the true class is a spoof. Neither is in the report. The 0.7 row above is accuracy over all five classes and mixes both.
- In production the model sees flagged windows only, a population selected by the detectors. The holdout is 20% `normal_track` by construction. The class balance in live flagged windows is unknown, so the holdout numbers do not transfer as rates.

## How the agent uses it

Constants and logic are in `agent/orchestrator/state_machine.py`.

- `PATTERN_MIN_CONFIDENCE = 0.7`. Uncalibrated placeholder. The benchmark above exists but the constant has not been reviewed against it, so treat 0.7 as still unset. The temperature values in `rl_agent_config.json` (1.6255, 1.2, 1.2) are Laya's own calibration for its three question types, not something GHAST tuned.
- `_pattern_vote(evidence, hypothesis)` returns `"contradicts"` if the tool was available at confidence of at least 0.7 and the label is `normal_track`; `"agrees"` if, at that confidence, the label is one the hypothesis implies (`HYPOTHESIS_IMPLIED_PATTERNS`: `freeze_replay` needs `freeze_replay`; `targeted_spoof` accepts `teleport_jump`, `gradual_drift` or `impossible_kinematics`; `equipment_fault` has no agreeing label); otherwise `None` (unavailable, low confidence, no tool, or a confident label for a different pattern).
- The vote is used in exactly one place, `_apply_single_detector_cap`:

| Situation | Effect |
|---|---|
| confident `normal_track`, hypothesis is not `jamming`, `benign`, `unresolved` | confidence capped at 0.5, even with several detector votes |
| confident label that matches the hypothesis and exactly one detector vote | cap not applied |
| confident non-normal label that does not match the hypothesis | unchanged (the cap still applies to a single vote) |
| any other case | unchanged |

- Exceptions: `jamming`, `benign` and `unresolved` are never touched. The sequence-corroborated `freeze_replay` tier ignores a `normal_track` contradiction (`ignore_contradiction=True`) but the single-detector cap still applies to it unless Laya agrees, which for this tier means a `freeze_replay` label.

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
| exporter (`queue` and `build`, real windows and human labels) | implemented, unit tested on hand-built windows; `queue` never run against the database |
| `data/laya/` | empty; no labeled real windows exist |
| fine-tuned model | trained once, on injected data that is no longer in the repo; weights not in the repo |
| holdout evaluation | recorded once (`benchmark_report.json`), on injected data only |
| fine-tune notebook | GHAST-adapted, committed; waits for labeled data |
| `pattern_classifier` tool, `investigate` and `form_hypothesis` wiring | implemented, tested with fakes |
| `load_laya_predictor` against the real library | not tested |
| `PATTERN_MIN_CONFIDENCE` calibration | not done |
| retrain on real labels, re-evaluate | not done, blocked on labels |
| evaluation on real incidents or real spoofing | not possible yet, no labels |
| effect on real incidents | unmeasured; `ghast_latest_report.md` shows no classifier vote in its evidence summary |

Known limitations:

- Injected-to-real gap. The model in use learned an injector's version of each pattern. Real spoofing may not look like any of the five. Until it is retrained on labeled real windows, treat its vote as unvalidated.
- The summary carries no vessel type or context. A fishing vessel manoeuvring and a spoofed track can look the same to it.
- Other real anomalies (timestamp problems, duplicate messages, AIS sentinel values) were never in its training data, so their summaries are out of distribution and the answer is undefined.
- Laya's own README lists negation and label-wording weaknesses, and its calibration slice comes from its training items.
- The five labels are the injector's taxonomy. Whether they are the right classes for real events is an open question that real labeling will answer.
