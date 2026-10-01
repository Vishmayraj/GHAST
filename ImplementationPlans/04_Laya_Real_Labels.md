# 04 Laya on real labels

Depends on: 01 (review CLI and the fixed vote rule). Labeling can start once the queue exists (already built).

## Why

The Laya model in use was trained on injected data that is removed. Its 0.83 holdout accuracy measures recovery of an injector's labels. The classifier needs real labeled windows, or it should stay out of decisions.

## Steps

1. Decide the label set with the owner. The current five (`normal_track`, `teleport_jump`, `gradual_drift`, `freeze_replay`, `impossible_kinematics`) were the injector's taxonomy. Real data may lack some of them. If a class has fewer than 50 real labeled examples after a fair effort, merge or drop it and change `PATTERN_LABELS` and `_CRITERIA` in `ml/features/summary.py` (one place, tests follow).
2. Write `docs/research_notes/laya-labeling-guide.md`: one paragraph per label with what the summary lines look like, plus "unclear, skip" as an allowed outcome. Two reviewers on a subset to measure agreement if there are two people.
3. Stratify the queue. A random sample of real windows is almost all normal. Extend `laya_export queue` with `--from-incidents` (windows around stored incidents) and `--flagged-only` (windows where a detector voted), then mix with a random normal sample. Keep the vessel split rule.
4. Label: a small CLI or a page in the dashboard (plan 02) that shows the summary text and the track, and writes `labels.jsonl`. `build` already validates and joins.
5. Fine-tune with `notebooks/laya_finetune_ghast_kaggle_2xT4.ipynb`. It reads `data/laya/train.jsonl` and `holdout.jsonl`; it does not need edits if the labels stay in the same schema. Copy the output into `ml/laya_model/`, store `model.safetensors` per plan 06.
6. Evaluate on the holdout and, more importantly, on reviewed incidents (plan 01). Report per-class accuracy, and the two asymmetric error rates the agent cares about: a confident non-normal answer on a truly normal window, and a confident `normal_track` on a truly spoofed one.
7. Calibrate `PATTERN_MIN_CONFIDENCE` (`agent/orchestrator/state_machine.py`, currently an unreviewed 0.7) against those two rates and record the choice.
8. Roll out in shadow mode first: the tool runs and its vote is stored in `evidence`, but `_apply_single_detector_cap` ignores it (a flag in config). Compare its votes with reviewer verdicts for a few weeks, then enable.

## Done when

- `data/laya/manifest.json` shows real counts per class, sources, and no synthetic label source.
- `docs/laya_pattern_classifier.md` "Recorded evaluation" describes the new model on real labels, and the removed injected benchmark is history only.
- Shadow-mode agreement with reviewer verdicts is reported before the vote changes any confidence.

## Not run without labels or a GPU

Steps 3 to 5 need people, the database and a Kaggle GPU. If none of that happened, say so and leave the status table unchanged.
