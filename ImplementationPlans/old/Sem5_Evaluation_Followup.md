# Sem 5, Stage 1: Fix the Evaluation Pipeline (Holdout, Memory, GPU, MLflow, Tests)

Status: ready to execute
Intended path in repo: `ImplementationPlans/Sem5_Evaluation_Followup.md`
Depends on: `ImplementationPlans/Sem5_Evaluation_And_Threshold.md` and the preliminary evaluation
run it produced (checkpoint `epoch_010.pt`, F1=0.396 vs. speed_jump baseline F1=0.214, threshold
candidate 0.005451, but with the caveats in section 0 below).

This is the second document in the post-training sequence. Do not start on a live scoring
service, agent wiring, or Laya integration in this pass, that is a separate document that follows
once every box in this one's section 8 is checked.

---

## 0. Why this document exists

The first evaluation run proved the wiring works end to end and produced a genuinely encouraging
first signal (prediction-error detector beat the speed-jump baseline on every pattern except
freeze/replay). But five things about how that run was produced mean its numbers, including the
0.005451 threshold candidate, are not trustworthy yet, and `OPERATING_THRESHOLD` in
`ml/models/bilstm/threshold.py` must stay `None` until this document's checklist is done:

1. The eval windows almost certainly overlapped the training data (same April range, no
   disjoint split), so the F1 numbers may be measuring memorization, not generalization.
2. Only 1,000 windows out of 13,863+ available vessels were scored, a small development sample.
3. 24.4% of unmodified control observations were flagged as spoofed at the chosen threshold,
   which is a real false-positive problem, not a rounding error.
4. MLflow logging was skipped entirely (`--no-mlflow`) after the run hit a tracking-backend
   error, so none of this is queryable or comparable to a future run yet.
5. The dedicated test file was written but never confirmed passing or wired into CI.

Fix all five. Then re-run the evaluator once, and only that corrected run's numbers are eligible
to become `OPERATING_THRESHOLD`.

## 1. The source-selection bug is not actually fixed in the repo

Check `ml/evaluation/score_checkpoint.py::run()` and `main()` right now: neither takes a
`--source` argument, and `load_training_windows(dsn, eval_start, eval_end)` is called with no
`source` keyword, so it silently defaults to `source="live"` (the default in
`features/pipeline.py`). Against a database whose only data is `message_type='historical'`, this
returns zero windows, exactly the failure the first run hit and worked around interactively.

Add `--source {live,historical}` to `score_checkpoint.py::main()`, matching the exact convention
`ml/training/train.py::main()` already uses for the same choice, and thread it through to every
call that currently omits it. This is a small, mechanical fix, do it first before anything else in
this document, since every later step's testing depends on the evaluator actually running against
real data on a fresh checkout.

## 2. Replace the full-materialize load with the shard/streaming discipline `dataset_cache.py` already proved out

This is the main fix, and it should resolve the GPU/RAM problem directly, not as a side effect.

Read `ml/training/dataset_cache.py` and `ml/training/train.py::_run_epoch`/`train_from_shards`
closely before writing anything here, they already solved this exact problem for training over
the same 31M-row range, and the evaluator should mirror the same shape rather than reinvent it:

- **Stream, don't materialize.** Replace `windows = await load_training_windows(dsn, eval_start,
  eval_end)` (which drains `stream_feature_windows()`'s async generator into one full Python
  list before anything else happens) with direct iteration over
  `features.pipeline.stream_feature_windows(...)`, the same generator `dataset_cache.py` already
  consumes. Never hold the full window list in memory at once.
- **Batch the injection and the model, not just the query.** Buffer streamed windows into
  fixed-size batches (reuse or match `training.dataset_cache.DEFAULT_SHARD_SIZE`'s spirit, a
  batch of a few hundred to a couple thousand windows is reasonable, make it a named constant).
  For each batch: run `features.inject.build_synthetic_dataset()` on just that batch, then score
  the whole batch in **one** forward pass through the model, stacking windows into the batch
  dimension the same way `ml/training/train.py::_batch()` already does for training
  (`torch.stack`/`torch.tensor` over a list of window feature arrays, moved to `device` once per
  batch, not once per window). `ml/models/bilstm/infer.py::prediction_errors()` currently takes
  one window and does `unsqueeze(0)` to fake a batch of size 1, this is the actual reason the GPU
  sees ~0% utilization in the preliminary run's own Section 3, not a red herring. Add a
  batched sibling (e.g. `prediction_errors_batch(model, windows) -> list[np.ndarray]`) that takes
  a batch of windows and scores them together in a single forward pass, following the exact
  device-handling fix the preliminary run already found and applied
  (`device = next(model.parameters()).device`, moving every input tensor to that device, not
  assuming CPU). Keep the existing single-window `prediction_errors()` too, other callers (a
  future live scoring service scoring one incoming window at a time) will want it; just don't use
  it in a tight loop over thousands of windows.
- **Keep only the lightweight output.** Flatten each batch's `InjectedWindow` results into
  `AISObservation` rows immediately after scoring and keep only those (accumulate the list of
  `AISObservation`, which is small, across batches for the final threshold sweep), discard each
  batch's raw `FeatureWindow`/`InjectedWindow` objects once flattened. Tens of thousands of
  `AISObservation` rows in memory is fine, tens of thousands of full window feature arrays plus
  their injected duplicates is not.

## 3. Profile before declaring this fixed, don't just eyeball Task Manager again

The preliminary run's RAM-usage read (constant 95%+) was never actually diagnosed, only
Windows Task Manager's GPU graph was checked, and that graph can misread small/brief GPU ops as
0% even when CUDA is genuinely being used, as the report's own Section 3 already notes for the
old unbatched code. Do the equivalent diagnosis for RAM before and after step 2's fix:

- Log the evaluator process's own RSS (`psutil.Process().memory_info().rss`, or
  `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` if avoiding a new dependency) every N
  batches, printed alongside the existing progress output.
- Separately note whatever the OS-level "95% used" figure was measuring, Windows (and Linux page
  cache) can report high "used" memory that's actually reclaimable disk/Postgres cache, not a
  leak in the Python process. Report the process RSS number and the OS-level number side by side
  in this document's write-up so the distinction is on record, not re-litigated next time.
- If process RSS is still high after step 2, check whether the asyncpg cursor in
  `features/pipeline.py`'s streaming query has an unbounded or very large `prefetch` size, and
  cap it explicitly if so, that would explain the client buffering far more rows than one batch
  needs even with a generator-shaped API on top.
- Confirm GPU utilization directly, not via Task Manager: `torch.cuda.memory_allocated()` and
  `torch.cuda.utilization()` (or `nvidia-smi` in a loop) bracketing a batch of forward passes,
  logged into this document's write-up.

## 4. A genuine held-out eval set, without needing new data

The imported historical range is `2026-04-01` through roughly `2026-04-15/16`, the same window
used for training, so hunting for a disjoint calendar range within it won't work. Use the
vessel-level split the checkpoint's own training already relied on instead:

Import `is_validation_vessel` from `training.dataset_cache` (it already exists, exactly for this:
`is_validation_vessel(mmsi, validation_fraction=0.2, buckets=1000)`, a deterministic hash of MMSI
alone, no vessel list needed up front). `ml/training/train.py::run_training` never overrides
`validation_fraction`, so every checkpoint trained so far used the default 0.2. Filter the
streamed windows in step 2 to only vessels where `is_validation_vessel(window.mmsi)` is `True`
before building the synthetic eval set. This guarantees zero vessel-level overlap with whatever
the checkpoint trained on, using the exact same split logic that produced its own `val_loss`
number, without needing a new data import.

Log this explicitly, both in the console output and in the MLflow run (step 6):
`holdout_method = "validation_vessel_split (fraction=0.2)"`, so a future reader knows exactly what
disjointness guarantee this run does and doesn't have.

## 5. Record training provenance in future checkpoints

`epoch_010.pt`'s checkpoint dict has `epoch`, `model_state`, `optimiser_state`, `train_loss`,
`val_loss`, nothing recording what data trained it. Fix this going forward, not retroactively (the
vessel-split holdout in section 4 sidesteps the need to know epoch_010's exact range anyway):

- In `ml/training/train.py::train_from_shards`, add `source`, `start` (isoformat), `end`
  (isoformat) to the checkpoint dict already being written each epoch.
- In `score_checkpoint.py::load_checkpoint`, read these with `.get(...)` and print
  `"unknown (checkpoint predates provenance fields)"` for older checkpoints missing them, don't
  crash or silently show blank values.

## 6. Fix MLflow, don't route around it with `--no-mlflow`

The error the preliminary run hit (`"filesystem tracking backend ... is in maintenance mode"`) is
a known, current MLflow 3.x behavior: the legacy bare `file:` tracking store is deprecated and now
refuses to open without an explicit opt-out. The correct fix is not to set that opt-out
(`MLFLOW_ALLOW_FILE_STORE=true`) to keep using a deprecated store, it's to point at a local SQLite
backend instead, which is MLflow's own current recommended default:

- Change `score_checkpoint.py::DEFAULT_MLFLOW_TRACKING_URI` from `"file:mlruns"` to
  `"sqlite:///mlruns/mlflow.db"` (or an equivalent path under the repo).
- Add `mlruns/` to `.gitignore` if it isn't already (this is local run data, not something to
  commit).
- Re-run the evaluator without `--no-mlflow` and confirm a real run appears (`mlflow ui
  --backend-store-uri sqlite:///mlruns/mlflow.db`, or just inspect the sqlite file's `runs` table)
  before considering this step done.

## 7. Verify the existing tests, don't just assume the file is enough

`ml/evaluation/tests/test_score_checkpoint.py` already exists and looks well-built (CPU-fast,
untrained tiny model, no real DB or checkpoint required). Actually run it
(`cd ml && python -m pytest evaluation/tests/test_score_checkpoint.py -v`) and confirm it passes.
Then confirm `.github/workflows/ml-evaluation-tests.yml`'s path filters actually include
`ml/evaluation/score_checkpoint.py` and its test directory, not just the older
`harness.py`/`baselines.py`/`metrics.py` files, fix the filter if it's stale. If step 2 adds a new
batched scoring function, add a test for it specifically: assert batched and single-window scoring
produce the same result for the same window (within floating-point tolerance), so the batching
optimization can't silently change what gets scored.

## 8. Re-run at real scale, and read the per-pattern results honestly

Once sections 1-7 are done, re-run the evaluator with the streaming/batched/held-out pipeline,
without the development `max_windows=1000` cap (or with it raised substantially, tens of
thousands of windows), against the validation-vessel slice of the imported April range. Then:

- Report the same per-pattern breakdown the preliminary run produced (control, and each of the
  four spoof patterns), on this corrected, larger, genuinely held-out set.
- Look specifically at `impossible_kinematics` (0.096 precision in the preliminary run, the worst
  result) and the control group (24.4% false-positive rate). Pull a handful of the control
  group's false positives and inspect them by eye, not just the aggregate number, to check
  whether they're the legitimate sharp-turn case the preliminary run's Section 8.3 speculated
  about.
- If the false-positive rate on control observations is still high on the corrected run, report
  that honestly rather than picking a threshold that makes the aggregate F1 look better while
  hiding it. A high control false-positive rate is a real finding that may mean a single global
  threshold isn't enough (e.g. per-vessel-class or per-region normalization might be needed later)
  and should carry forward into the next document, not get papered over here.

## 9. Definition of done

Status note (this pass): items 1-3, 5 and 6 are verified done by reading the current code on a
fresh checkout. Item 4's logging exists in code but hasn't produced a real-run number yet. Items
7-9 need a live Postgres/TimescaleDB instance and (for GPU numbers) a CUDA device to actually
execute `score_checkpoint.py` against; this pass had neither (no DB, no installed ML deps, and
per instruction no new installs), so they're left unchecked rather than marked done on the strength
of code review alone. `OPERATING_THRESHOLD` is correctly still `None` in `threshold.py` - do not
set it without that real run.

- [x] `score_checkpoint.py` takes an explicit `--source` argument and no longer silently defaults
      to `"live"`. (It still *defaults* to `"live"` when `--source` is omitted, matching
      `train.py::main()`'s own convention for the same choice - the bug was that "historical"
      couldn't be selected at all, not that live isn't the default.)
- [x] Windows are streamed and processed in bounded batches, mirroring
      `ml/training/dataset_cache.py`; `load_training_windows()`'s full-materialize call is no
      longer used in the evaluation path.
- [x] A batched scoring function exists in `ml/models/bilstm/infer.py`, used by
      `score_checkpoint.py` instead of one-window-at-a-time calls; a test confirms it matches
      single-window scoring.
- [ ] Process RSS and GPU utilization are both logged and reported for a real run, with the
      OS-level "95% used" figure explicitly distinguished from process RSS in the write-up.
      (Logging itself - `_profile()` in `score_checkpoint.py` - is in place; no real run has
      produced the numbers to report yet.)
- [x] The eval set is built from `is_validation_vessel`-filtered windows only, logged explicitly
      as the holdout method, both in console output and in MLflow.
- [x] `train.py` checkpoints going forward record `source`/`start`/`end`; `score_checkpoint.py`
      reads them gracefully (missing-field fallback, not a crash) for older checkpoints.
- [ ] MLflow points at a SQLite backend, not the deprecated filesystem store; a real run is
      confirmed logged (not skipped with `--no-mlflow`). (`DEFAULT_MLFLOW_TRACKING_URI` is already
      `sqlite:///mlruns/mlflow.db` and `mlruns/` is gitignored; the "confirmed logged" half still
      needs an actual run.)
- [ ] `test_score_checkpoint.py` confirmed passing locally, and CI's path filters confirmed to
      actually cover it. (`.github/workflows/ml-evaluation-tests.yml` does watch
      `ml/evaluation/**`, `ml/features/**` and `ml/models/bilstm/**`, so the filter itself looks
      correct on inspection; nothing in this pass actually ran pytest to confirm green.)
- [ ] A corrected evaluation run completed at real scale (not the 1,000-window development cap),
      with per-pattern results reported honestly, including if the control false-positive rate is
      still high.
- [ ] Only after all of the above: `OPERATING_THRESHOLD` in `ml/models/bilstm/threshold.py`
      updated from `None` to this corrected run's chosen value, with its provenance comment fully
      filled in (checkpoint, holdout method, eval scale, MLflow run ID, precision/recall/F1,
      beats-baseline verdict).

Do not start a live scoring service, agent wiring, or Laya integration until every box above is
checked. Those remain separate documents that follow this one. (Note: `agent/orchestrator/` and
`agent/tools/freeze_corroboration.py` already exist and already import `OPERATING_THRESHOLD` -
that work happened ahead of this gate; it degrades safely, since `form_hypothesis` treats
`OPERATING_THRESHOLD is None` as "unresolved, escalate for human review" rather than guessing. It's
flagged here rather than unwound, since undoing already-committed, safely-degrading work isn't
obviously the right call - but the sequencing this document asked for wasn't followed.)
