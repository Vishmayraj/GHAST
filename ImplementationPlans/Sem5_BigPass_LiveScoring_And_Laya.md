# Sem 5, Stage 1: Big Pass — Finalize Threshold, Fix the Agent, Live Scoring, Laya

Status: ready to execute
Intended path in repo: `ImplementationPlans/Sem5_BigPass_LiveScoring_And_Laya.md`
Depends on: `ImplementationPlans/Sem5_Evaluation_Followup.md` and its completed run (checkpoint
`epoch_010.pt`, held-out `validation_vessel_split` evaluation, ~6.4M scored observations,
prediction-error detector F1=0.424 @ threshold=0.004946, beats speed_jump baseline F1=0.224,
logged in MLflow experiment `bilstm-checkpoint-scoring`).

This is one large session covering several pieces that now genuinely belong together: the
evaluation is solid enough to build on, and the agent's placeholder logic can't stay as a guess
now that real numbers exist to replace it with. Work top to bottom, each section's output feeds
the next. Out of scope for this pass, do not start: `backend/api/`, the dashboard, DBSCAN/Stage 2
clustering, LangGraph. These remain separate future work.

---

## 1. Finalize `OPERATING_THRESHOLD` (do this first, five minutes, everything else assumes it's done)

`ml/models/bilstm/threshold.py` still has `OPERATING_THRESHOLD: float | None = None`. Update it to
the followup run's result, with the provenance comment fully filled in:

```python
OPERATING_THRESHOLD: float | None = 0.004946
```

Provenance block: checkpoint `epoch_010.pt` (epoch 10, train_loss=0.0006055,
val_loss=0.0007673), holdout method `validation_vessel_split` (fraction=0.2, via
`training.dataset_cache.is_validation_vessel`), full evaluation scale (~6.4M observations across
control + 4 spoof patterns), chosen tradeoff max F1, precision/recall/F1 at this threshold, verdict
beats `speed_jump_detector` (0.424 vs 0.224), and the MLflow experiment name
`bilstm-checkpoint-scoring` (record the specific run ID if it's easy to look up, don't block on it
if not). Everything downstream in this document imports this constant rather than repeating the
number, so get this right once here.

## 2. Fix `form_hypothesis`'s placeholder benign threshold (a real bug, not a style nit)

`agent/orchestrator/state_machine.py::form_hypothesis` currently has:

```python
if anomaly.anomaly_score < 0.3: return "benign", 0.75
```

This predates any real evaluation and doesn't match the model's actual output scale at all,
prediction errors from the followup run range from about 0.0009 up to 14.4, heavily right-skewed,
nothing like a 0-1 normalized score. As written, this branch calls almost everything "benign."

Fix it to reference the real threshold instead of a guessed constant:

```python
from models.bilstm.threshold import OPERATING_THRESHOLD
...
if OPERATING_THRESHOLD is not None and anomaly.anomaly_score < OPERATING_THRESHOLD:
    return "benign", 0.75
```

Add a fallback for `OPERATING_THRESHOLD is None` (shouldn't happen after section 1, but
`form_hypothesis` shouldn't silently misclassify if it does, raise or route to `ESCALATING`
unconditionally in that case rather than guessing). Add a unit test in `agent/tests/` asserting a
`FlaggedAnomaly` with `anomaly_score` just below `OPERATING_THRESHOLD` resolves to `benign`, and
one just above does not, this is exactly the kind of off-by-a-guessed-constant bug that's easy to
silently reintroduce later without a test pinning it down.

## 3. Add complementary detectors the per-pattern results justify

The followup run's per-pattern breakdown showed the prediction-error detector is strong on
gradual drift (F1=0.793) and weak specifically where the theory already predicted it would be:
freeze/replay (F1=0.244, a frozen position doesn't necessarily produce a large next-step
prediction error) and impossible_kinematics (precision=0.089, likely confusable with legitimate
sharp maneuvers). `docs/research_notes/ml-data-strategy.md`'s own injector design table anticipated
this. Build on it rather than relying on one detector alone:

- **`ml/evaluation/baselines.py::freeze_replay_detector`** — a new `Detector` function, no model
  needed. Given an `AISObservation` sequence for a vessel/window, flag near-identical consecutive
  positions (within a small epsilon) reported alongside nonzero `sog`, the specific signature
  prediction-error alone misses. Match the existing `Detector` protocol shape
  (`AISObservation -> float`) so it plugs into `harness.evaluate()` the same way
  `speed_jump_detector` already does; add it to `score_checkpoint.py`'s sweep alongside the
  existing two detectors and report its per-pattern F1 the same way, specifically checking whether
  it improves on freeze/replay's weak 0.244.
- **A combined evidence signal for the live scoring service (section 4), not a single float.**
  Rather than reducing every window to one `anomaly_score`, have the scorer record which
  detector(s) fired (prediction-error over threshold, freeze/replay pattern matched, speed_jump
  over its own threshold) into `FlaggedAnomaly`'s evidence, and pass that richer evidence into
  `form_hypothesis` so corroboration across detectors, not just prediction-error alone, can inform
  confidence. This directly targets the followup run's other finding, the 19.4% control
  false-positive rate, a flag that only prediction-error fired on an otherwise-unremarkable window
  is weaker evidence than a flag where two independent detectors agree; encode that distinction
  rather than losing it. This is a first pass at the mitigation, not a claim that it fully solves
  the false-positive rate, note in the document's own write-up (section 6) that a proper fix
  (per-vessel-class or per-region threshold normalization) remains future work.

## 4. Build the live scoring service (`scoring/`)

This is the piece that's been missing since the very first manifesto stubbed it out as "not in
scope." Everything it needs already exists, this section is glue, not new modeling work.

New top-level directory `scoring/` (sibling to `ingestion/`, `ml/`, `agent/`, `backend/`, matching
the repo's existing layering):

- **`scoring/live_scorer.py`** — the main loop. For Stage 1, a polling design is fine (a streaming
  trigger off `ingestion`'s writes is a Stage 2 optimization, not needed yet): periodically query
  `vessel_position` for vessels with new reports since the last check (`message_type != 'historical'`,
  matching the live/historical separation `ml/features/pipeline.py`'s `TrainingDataSource` already
  encodes), pull each such vessel's most recent `WINDOW_LENGTH` (20, `features/pipeline.py`)
  reports via the same query shape `agent/tools/track_history.py` already uses, and build a
  `FeatureWindow` the same way `ml/features/extract.py`/`pipeline.py` already do for training,
  don't write a second, parallel feature-extraction path.
- Score each window with `ml/models/bilstm/infer.py::prediction_errors()` (the single-window path,
  this is online per-vessel scoring, not the batch path built for offline evaluation) plus the new
  `freeze_replay_detector` and the existing `speed_jump_detector` from section 3, using
  `OPERATING_THRESHOLD` from section 1 as the trigger.
- When the combined evidence crosses the trigger, construct a
  `agent.orchestrator.state_machine.FlaggedAnomaly` and call `investigate()`, passing the same
  three tools (`agent/tools/track_history.py`, `jamming_zones.py`, `incident_history.py`) and
  `agent/report_generator/report.py::draft_report` already built. Don't reimplement any of this,
  `scoring/live_scorer.py`'s job ends at constructing the `FlaggedAnomaly` and calling the agent
  that already exists.
- A vessel already under an open, unresolved incident (check `incidents` for a recent unresolved
  row for the same `mmsi`) shouldn't spawn a duplicate investigation on every poll cycle, debounce
  on that.
- CLI entrypoint (`scoring/live_scorer.py`'s `main()`) taking `--dsn`, `--checkpoint`,
  `--poll-interval-seconds`, matching the argument style already established across
  `ingestion/main.py`, `ml/training/train.py`, `ml/evaluation/score_checkpoint.py`.
- Add a `scoring` service to `infra/docker/docker-compose.yml`, matching how `ingestion` is
  already wired in.
- `scoring/tests/`: mock the DB and the agent's tools, assert a window with a known high
  prediction-error triggers `investigate()` and one with a known low error doesn't, plus the
  debounce behavior. No real DB, model checkpoint, or Claude API key needed in CI, matching every
  other test suite in this repo.

## 5. Fix the stale model string while touching `report_generator/` anyway

`agent/report_generator/report.py` hardcodes `model="claude-sonnet-4-20250514"`, a specific dated
snapshot that's already stale. Since section 4 touches the agent's call path, fix this now instead
of leaving it for later: read the model name from an environment variable
(`GHAST_REPORT_MODEL`, or similar) with a documented current default, rather than a hardcoded
string, so it doesn't silently go stale again the next time Anthropic ships a new model. Check
`docs.claude.com`'s current model list for what the default should be at the time this is written,
don't guess a string from training data.

## 6. Laya: fine-tune on the injector's own pattern labels, add as a fifth evidence source

This is the integration point identified earlier, now genuinely well-supported by real scale.
`ml/evaluation/score_checkpoint.py::score_injected_windows` already carries `injected.pattern`
through into every `AISObservation.pattern` field (`teleport_jump` / `gradual_drift` /
`freeze_replay` / `impossible_kinematics` / `None` for control), and the followup run already
produced roughly 6.4M such labeled rows. This is real, already-generated, already-labeled training
data for exactly the `choice` classification task Laya is built for, no incident history or
chicken-and-egg problem needed.

- Export a fine-tuning set from a `score_checkpoint.py` run: `(feature summary or raw window,
  pattern label)` pairs, using `control` as a fifth class alongside the four spoof patterns. Don't
  export all 6.4M rows verbatim, sample down to a reasonable fine-tuning set size (check Laya's
  own docs/examples for what scale it expects) balanced roughly evenly across the five classes,
  not proportional to the raw counts (the injector's `CONTROL_FRACTION`, `features/inject.py`,
  already skews the raw distribution).
- Fine-tune a Laya `choice` model on this set, self-hosted per Laya's own setup instructions
  (github.com/NandhaKishorM/laya), free.
- Add `agent/tools/pattern_classifier.py`, a fourth tool matching the existing `Tool` protocol in
  `agent/orchestrator/state_machine.py`, calling the fine-tuned Laya model on the flagged window's
  features and returning its `choice` output plus confidence as a fourth evidence source.
- Wire it into `investigate()`'s tool list (currently `("track_history", "jamming_zones",
  "incident_history")`) and into `form_hypothesis`, Laya's pattern tag corroborating or
  contradicting the ML/rule-based signals from section 3 is exactly the kind of second, fast,
  free, independent vote that can help with the control false-positive problem noted in section 3,
  without waiting for real incident history to accumulate first.
- This is the one place in this whole pass that adds a new external dependency, keep it isolated:
  if Laya's local setup doesn't work cleanly in the time available, ship sections 1-5 without it
  and leave `pattern_classifier.py` as a clearly-marked stub returning a neutral/unknown result,
  don't let it block the rest of this document.

## 7. Definition of done

- [ ] `OPERATING_THRESHOLD = 0.004946` in `threshold.py`, full provenance comment filled in.
- [ ] `form_hypothesis`'s benign branch uses `OPERATING_THRESHOLD`, not the old `0.3` constant; a
      test pins the boundary behavior.
- [ ] `freeze_replay_detector` added to `baselines.py`, swept in `score_checkpoint.py` alongside
      the existing two detectors, its per-pattern F1 (especially on freeze/replay) reported.
- [ ] `scoring/live_scorer.py` exists, polls live (non-historical) `vessel_position` data, scores
      with the existing model + detectors, and calls the existing `agent.investigate()` on a
      real trigger, with debouncing against duplicate open incidents.
- [ ] `scoring` service added to `infra/docker/docker-compose.yml`.
- [ ] `scoring/tests/` passing, with a CI workflow entry matching the existing pattern.
- [ ] `report_generator/report.py`'s model string reads from an environment variable with a
      current, verified default, not a hardcoded stale one.
- [ ] Laya fine-tuned on the injector's real pattern-labeled data (or, if blocked, a clearly
      stubbed `agent/tools/pattern_classifier.py` with a note on what's blocking it) and wired
      into `investigate()`'s tool list and `form_hypothesis`.
- [ ] A short written verdict at the end: with sections 1-6 in place, run the live scorer against
      whatever live data has accumulated since ingestion started and confirm at least one full
      cycle, flag through hypothesis to either a drafted report or an escalation, actually
      completes on real (not synthetic) data. This is the first time anything in this repo will
      have run against real live traffic end to end, report honestly if it doesn't fire (not
      enough live anomalies yet is a legitimate outcome, not a failure) or if it does.

Backend API and dashboard wiring remain the next document after this one, once section 7 is fully
checked.
