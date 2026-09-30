# Laya pattern classifier: export, fine-tune, evaluate, enable

Status: code is in place and tested with fakes. No checkpoint has been fine-tuned or
evaluated yet, so until you do steps 1 to 3 the tool is a neutral stub and changes nothing.

What it is: a Laya `choice` model that reads a short text summary of a flagged 20-report
window and picks one of five classes: normal_track, teleport_jump, gradual_drift,
freeze_replay, impossible_kinematics. In `form_hypothesis` it can only do two things: lift
the single-detector confidence cap when it confidently names a spoof pattern, or apply the
cap when it confidently says normal_track. It never picks the hypothesis and never forces
"benign".

## 1. Export the training data

From `ml/`, with the database reachable (no torch or checkpoint needed):

    python -m evaluation.laya_export --dsn $POSTGRES_DSN --source historical \
        --start 2026-04-01 --end 2026-04-15 --out-dir ../data/laya

Writes `train.jsonl`, `holdout.jsonl`, `manifest.json`. Classes are balanced by
construction (default 1500 per class for train, 300 per class for holdout). Splits are by
vessel with the same rule the BiLSTM used, so holdout vessels were never seen in training.
If the manifest says `quota_met: false`, widen the date range or raise `--sample-permille`.

Each row has `state`, `questions`, `gold` as JSON strings, the schema Laya's own notebook
reads. The text in `state` comes from `ml/features/summary.py`, the same function the live
tool uses, so training and serving match.

## 2. Fine-tune

Use Laya's Kaggle notebook (`notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb` in
github.com/NandhaKishorM/laya), free on 2x T4. Upload `train.jsonl` and `holdout.jsonl` as a
Kaggle dataset, then replace the two `load_dataset("LocalLLaMA/typed-decisions", ...)` calls
with `load_dataset("json", data_files=...)` on those files. Keep the row schema as is.
Save the output directory it writes (model.safetensors, encoder/, tokenizer/,
rl_agent_config.json).

## 3. Evaluate before trusting it

Run the fine-tuned model over `holdout.jsonl` and record, at minimum: per-class accuracy, the
confusion matrix, and how often true normal_track rows are called a spoof (that is the number
that matters for the 19.4% control false-positive problem). Chance is 0.20 for five classes.
Write the results, and the confidence level where accuracy is high enough to trust, next to
`PATTERN_MIN_CONFIDENCE` in `agent/orchestrator/state_machine.py`, which is an uncalibrated
placeholder (0.7) until you do this.

## 4. Enable

Copy the checkpoint directory to `ml/laya_model/`, then in `infra/docker/.env`:

    WITH_LAYA=1
    GHAST_LAYA_MODEL=/laya_model

Rebuild the `scoring` image. Locally without Docker: `pip install laya`, then pass
`--laya-model path/to/checkpoint` to `scoring/live_scorer.py`. If the model fails to load the
scorer logs it and continues with the stub.

## Known limits

- Synthetic to real gap. The model learns the injector's version of each pattern. The
  injector's `freeze_replay` copies earlier positions forward (the track loops back over
  ground already covered); it does not usually hold one position still. The summary has a
  field for each case (`reports_repeating_earlier_positions`, `steps_claiming_speed_but_not_moving`),
  but real spoofing may look different from both.
- Worth checking in the next evaluation run: `freeze_replay_detector` looks for stationary
  claims, which the injected freeze_replay class rarely produces. That may be part of why its
  per-pattern F1 was weak, and would be a mismatch in the test data rather than in the detector.
- Laya's own README lists negation and label-wording weaknesses, and its calibration slice is
  drawn from training items. Do not treat its confidences as calibrated until step 3 is done.
- The live tool classifies the 20 reports ending at the flagged report, which can differ
  slightly from the window the BiLSTM scored.
