# Sem 5, Stage 1: Evaluate the Trained Checkpoint and Select an Operating Threshold

Status: ready to execute
Intended path in repo: `ImplementationPlans/Sem5_Evaluation_And_Threshold.md`
Depends on: a completed training run with a checkpoint on disk (the repo already has one:
`checkpoints/epoch_010.pt`, train_loss=0.000606, val_loss=0.000767, per
`docs/research_notes/` or whatever the historical-training report got committed as).
This is the first of a sequence of hand-off documents; do not start on live scoring, the agent's
wiring, or Laya in this pass — those are separate documents that follow this one once its
checklist is fully checked.

---

## 0. Why this document exists

Ten completed training epochs prove the pipeline runs end to end. They do not prove the model
detects spoofing. `ml/models/bilstm/infer.py::prediction_errors()` (the scoring function) and
`ml/evaluation/datasets.py::load_injected_synthetic` (the labeled synthetic eval set) both already
exist, but nothing connects them: no script has ever populated an `AISObservation.prediction_error`
field with this model's output, so `ml/evaluation/harness.py::evaluate()` has never been run
against this checkpoint. There is currently no evidence, one way or the other, that the model beats
`ml/evaluation/baselines.py::speed_jump_detector`. That evidence is this document's deliverable.

## 1. A real, currently-unfixed bug to work around, not to fix

`ml/evaluation/harness.py::main()` calls every registered dataset loader the same way:
`load(args.path)`. That matches `load_gps_spoofing_mass(path)`, a synchronous function taking one
positional path argument. It does not match `load_injected_synthetic(dsn, start, end, seed=0)`, an
async function with a completely different signature. Calling the CLI with
`--dataset injected_synthetic` today passes a path string in as `dsn` and returns an unawaited
coroutine object instead of a list of observations.

Do not fix this by changing `harness.py`. The core function it exports, `evaluate(observations,
detector, threshold) -> EvaluationResult`, is correct and well-tested; only its CLI wrapper assumes
a synchronous, single-argument loader. The script this document asks for calls `evaluate()`
directly, with its own async data loading and model inference in front of it, and never goes
through `harness.py`'s `main()` for the synthetic path. `harness.py`'s existing CLI keeps working
exactly as before for `gps_spoofing_mass`; nothing about it changes.

## 2. Build `ml/evaluation/score_checkpoint.py`

This is the one new file this document asks for. Responsibilities, in order:

1. **Load the checkpoint.** `torch.load(checkpoint_path, map_location=device)` gives a dict with
   keys `epoch`, `model_state`, `optimiser_state`, `train_loss`, `val_loss` (see
   `ml/training/train.py::train_from_shards`, which writes exactly this shape). Only `model_state`
   is needed for scoring. Reconstruct the model with `BiLSTMNextDelta(N_FEATURES)` (import
   `N_FEATURES` from `features.extract`, same as `train.py` does — hidden size is never
   overridden anywhere in the codebase, so the default is correct), then
   `model.load_state_dict(checkpoint["model_state"])`, then `model.eval()`.

2. **Build the injected synthetic eval set.** Call `features.pipeline.load_training_windows(dsn,
   start, end)` for a time range, then `features.inject.build_synthetic_dataset(windows, seed=...)`
   directly (both already exist and are what `load_injected_synthetic` calls internally — this
   script needs the intermediate `InjectedWindow` objects, not the flattened `AISObservation` list,
   because scoring needs the window structure, not point-level rows). Use a date range that is
   disjoint from whatever range trained the checkpoint (the historical run used
   2026-04-01 to 2026-04-16 — pick a different April window, or a live window if enough live data
   exists by the time this runs, so the eval set isn't windows the model already saw).

3. **Score every window.** For each `InjectedWindow`, call
   `ml/models/bilstm/infer.py::prediction_errors(model, injected.window)` to get a per-timestep
   error array. Flatten into `AISObservation` rows the same way
   `datasets.py::load_injected_synthetic` already does, but this time set `prediction_error` to
   the real scored value instead of leaving it `None`, and additionally carry `injected.pattern`
   through as an extra field if convenient (not required for this document, but the next document
   in this sequence — the Laya one — will want it, so it costs nothing to keep now rather than
   re-deriving it later).

4. **Sweep thresholds and call `evaluate()` directly.** Import `evaluate` from
   `ml.evaluation.harness` and `prediction_error_detector` from `ml.evaluation.baselines`. Run
   `evaluate(observations, prediction_error_detector, threshold)` across a range of thresholds
   (e.g. a log-spaced or percentile-based sweep over the observed prediction-error distribution,
   not a linear guess — the errors from a trained model are not on a scale anyone can guess in
   advance). Print precision/recall/F1 at each. Also run `evaluate(observations,
   speed_jump_detector, threshold)` across its own sweep on the same observations, as the baseline
   comparison the original manifesto's definition-of-done required.

5. **Watch false positives on the control group specifically.** `build_synthetic_dataset` already
   reserves a `CONTROL_FRACTION` of windows as unmodified controls (`pattern is None`,
   `is_spoofed` all `False`). Report precision/recall broken out by pattern type in addition to
   the aggregate number — the historical training report's Section 22 already raised the concern
   that legitimate sharp turns could look like spoofing to a naive threshold; the control group is
   exactly what would catch that, and an aggregate F1 alone can hide a bad false-positive rate on
   real, unremarkable maneuvers.

6. **Pick a threshold and write it down as a named constant, not a magic number.** Once a sweep is
   run, choose the threshold that maximizes F1 (or, if precision matters more for this application
   given the agent's `ESCALATING` branch exists as a safety net for uncertain cases, whichever
   precision/recall tradeoff seems right — this is a judgment call the sweep's printed output makes
   possible, not something this document dictates). Store it somewhere a later document's live
   scoring service can import, e.g. `ml/models/bilstm/threshold.py` with a single documented
   constant, `OPERATING_THRESHOLD`, and a comment recording which checkpoint and eval run it came
   from.

## 3. Wire up MLflow (already a dependency, currently unused)

`ml/requirements.txt` has had `mlflow>=2.11` in it since Section 0 of the original manifesto, but
`ml/training/train.py` has never imported it. Add MLflow logging to `score_checkpoint.py`
(evaluation logging matters more right now than training-loss logging, since evaluation is what's
missing):

- One MLflow run per scoring pass, logging: the checkpoint path/epoch scored, the eval date range,
  the full threshold sweep (precision/recall/F1 per threshold, per detector), the chosen
  `OPERATING_THRESHOLD`, and the per-pattern breakdown from step 2.5 above.
- Point `MLFLOW_TRACKING_URI` at a local file store under the repo (e.g. `ml/mlruns/`, gitignored)
  for now — a hosted tracking server is a Stage 2+ concern, not blocking here.
- Retrofit `ml/training/train.py` with the same logging in a small follow-up (log
  hyperparameters and the loss curve per epoch) once this script proves the MLflow wiring works;
  don't block this document's deliverable on also rewriting `train.py`.

## 4. Tests

`ml/evaluation/tests/test_score_checkpoint.py` (new directory if it doesn't exist): a CPU-fast test
using a tiny in-memory model (untrained, random weights are fine) and a handful of synthetic
windows, asserting the scoring function populates `prediction_error` on every observation, the
threshold sweep runs without error across the full range, and the control-group breakout correctly
separates `pattern is None` rows from the rest. No real database or real checkpoint needed for
this test — mirror the CPU-only smoke-test discipline `ml/models/bilstm/tests/test_model.py`
already uses.

## 5. Definition of done

- [ ] `ml/evaluation/score_checkpoint.py` written, scoring the real `epoch_010.pt` checkpoint (or
      whichever is latest at the time this runs) against a real `injected_synthetic` eval range.
- [ ] A threshold sweep has actually been run and printed, broken out by spoof pattern and by
      detector (`prediction_error` vs `speed_jump` baseline).
- [ ] `OPERATING_THRESHOLD` exists as a named constant in `ml/models/bilstm/threshold.py`, with a
      comment recording its provenance (checkpoint, eval range, chosen precision/recall tradeoff).
- [ ] At least one MLflow run exists under `ml/mlruns/` (or wherever `MLFLOW_TRACKING_URI` points)
      with the full sweep and the chosen threshold logged.
- [ ] `ml/evaluation/tests/test_score_checkpoint.py` passes in CI, with a matching workflow entry
      added to `.github/workflows/` following the existing `ml-evaluation-tests.yml` pattern.
- [ ] A short written verdict: does `prediction_error_detector` (this model) actually beat
      `speed_jump_detector` (the baseline) on the injected synthetic set? If it does not, that is a
      valid and important outcome of this document, not a failure to hide — it means the next
      document's live scoring service needs to ship with the honest caveat that detection quality
      is still unproven, rather than silently going live on an unvalidated signal.

Do not proceed to a live scoring service, agent wiring, or Laya integration until every box above
is checked. Those are separate documents that follow this one.
