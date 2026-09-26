# Semester 5 Build Manifesto: ML Detection + Investigation Agent

Status: ready to execute
Intended path in repo: `ImplementationPlans/Sem5_ML_Agent_Manifesto.md`
Scope: Sem5IP.md's Weeks 7-13 (Detection model v1, Investigation agent v1), informed by
`docs/research_notes/ml-data-strategy.md` (read that first, its decisions are load-bearing here)
and the schema additions in `backend/models/schema.sql` (`incidents`, `jamming_zones`).

This document is written to be executed in one pass by an agent with local filesystem, terminal,
and Docker access (Claude Code / Codex) against the real repo, real TimescaleDB, and real
ingested AIS data. Every file path below is exact. Where a decision was already made elsewhere in
the repo, this document says so and does not re-litigate it. Where a decision is genuinely open,
it says that too and gives a default to take instead of stopping to ask.

---

## 0. Before writing any code

1. Confirm `docs/research_notes/ml-data-strategy.md` and the two new tables in
   `backend/models/schema.sql` (`incidents`, `jamming_zones`) are already in the repo. If not,
   they are prerequisites, not part of this manifesto, stop and get them in first.
2. Confirm the ingestion service has real data flowing: `docker compose -f
   infra/docker/docker-compose.yml up -d`, then check `vessel_position` has rows spanning more
   than a few hours (`SELECT count(*), count(DISTINCT mmsi), min(received_at), max(received_at)
   FROM vessel_position;`). If it's short of the 14-day / 20-reports-per-vessel floor set in the
   data strategy note, keep it running in the background and start with Sections 1-2 below
   (feature extraction, injector) while data accumulates, since neither needs 14 days of history
   to write and unit-test against synthetic fixtures.
3. Add `ml/requirements.txt` (does not exist yet):
   ```
   torch>=2.2
   mlflow>=2.11
   scikit-learn>=1.4
   numpy>=1.26
   pandas>=2.2
   asyncpg>=0.29
   tqdm>=4.66
   ```
   Mirror `ingestion/requirements-dev.txt`'s pattern for a matching `ml/requirements-dev.txt`
   with `pytest` for the new test suites.

---

## 1. `ml/features/` — feature extraction

Currently just a README. Build:

### `ml/features/extract.py`
Pure functions, no I/O (same discipline as `ingestion/normalizer/normalize.py`): given a list of
raw position rows for one vessel (already fetched), return per-timestep feature vectors.

- Input row shape: match what `vessel_position` actually stores (see `backend/models/schema.sql`):
  `received_at, mmsi, latitude, longitude, sog_knots, cog_deg, true_heading_deg, rate_of_turn,
  navigational_status`.
- Output features per HLD section 3.1: speed over ground, course over ground, heading, rate of
  turn, vessel class. Vessel class comes from `vessel_static.ship_type`, joined in by the caller
  (`ml/features/pipeline.py`, below), not by this module — keep `extract.py` free of DB access.
- AIS position reports arrive at irregular intervals (event-driven, not fixed-rate). Do not
  assume uniform timestep spacing. Compute derived features (e.g. implied speed between
  consecutive points, used later by the injector's "teleport jump" pattern) from actual elapsed
  time, not an assumed interval.
- Handle missing optional fields (`rate_of_turn`, `true_heading_deg` can be null per the
  normalizer) with an explicit sentinel/mask value, not silent zero-fill, so the model can learn
  "this field was missing" rather than "this field was zero."

### `ml/features/pipeline.py`
Fetches from TimescaleDB and windows into training-ready sequences.

- Query `vessel_position` (+ `vessel_static` for ship_type) via `asyncpg`, grouped by `mmsi`,
  ordered by `received_at`.
- Apply the data-volume gate from the strategy note: skip any vessel with fewer than 20 position
  reports in the query window.
- Slice each qualifying vessel's ordered reports into fixed-length windows (default window length
  20 — same floor as the per-vessel minimum, so every qualifying vessel yields at least one
  window; make this a constant, not a magic number, so training and the injector agree on it).
- Output: a list of `(mmsi, window_start, window_end, features: np.ndarray[window_len, n_features])`
  tuples. This is the shape both self-supervised training (Section 3) and the injector (Section 2)
  consume.
- Write `ml/features/tests/test_extract.py` covering: irregular timestep handling, missing-field
  masking, and the windowing boundary (a vessel with exactly 20 reports yields exactly one
  window, 19 yields none, 40 yields two non-overlapping windows — pick and document a stride,
  non-overlapping is the simpler default and fine for Stage 1).

---

## 2. Synthetic spoof injector

Per `docs/research_notes/ml-data-strategy.md` section 3. This is the actual unblocking work: it's
what turns real ingested data into something the eval harness can score against.

### `ml/features/inject.py`
- One function per pattern from the strategy note's table: `inject_teleport_jump`,
  `inject_gradual_drift`, `inject_freeze_replay`, `inject_impossible_kinematics`. Each takes a
  clean window (the `features` array from `pipeline.py`, plus the raw lat/lon it was derived
  from) and a `severity: float` in some documented range (e.g. 0.0-1.0), and returns a modified
  copy plus a per-point `is_spoofed: list[bool]` array.
- Every function takes an explicit `seed: int` (via `numpy.random.default_rng(seed)`, not global
  numpy random state) so a full synthetic dataset build is exactly reproducible from a single top-
  level seed, matching the reproducibility bar `ml/evaluation/harness.py` already holds real
  detector runs to.
- `ml/features/inject.py::build_synthetic_dataset(windows, patterns, severities, seed) ->
  list[InjectedWindow]` — the orchestrating function that takes clean windows from `pipeline.py`,
  applies a mix of patterns (including a "benign, no injection" control group — do not inject
  into every window, an eval set that's 100% spoofed can't measure false-positive rate) and
  severities, and returns labeled windows ready for Section 4's flattening step.
- Test with fixed seeds asserting byte-identical output across two runs, and asserting each
  pattern actually violates the physical-limits check it's meant to violate (e.g.
  `inject_teleport_jump` output implies a speed over the vessel-class max).

### Wire into the dataset registry
In `ml/evaluation/datasets.py`, add a new loader function (does not need to read from disk like
the existing loaders — it calls `ml/features/pipeline.py` + `ml/features/inject.py` directly) and
register it in `DATASET_LOADERS` as `"injected_synthetic"`. Its output must be `list[AISObservation]`
with `prediction_error` left `None` at this stage — that field gets populated in Section 4, after
a trained model exists to compute it. Flattening a window back to point-level `AISObservation`
rows (one per timestep) is deliberate — see `ml-data-strategy.md` section 4 for why this keeps
`harness.py` untouched.

---

## 3. `ml/models/bilstm/` — the model

Currently just a README describing intent. Build:

### `ml/models/bilstm/model.py`
- PyTorch `nn.Module`. Bidirectional LSTM encoder over a window of features (Section 1's output
  shape), predicting the next timestep's state (position delta, or full next state — position
  delta is the simpler and more physically interpretable target, prefer it unless it
  underperforms badly in early experiments).
- Vessel class (categorical, from `vessel_static.ship_type`) enters as an embedding concatenated
  into the per-timestep input, not as a separate model branch — keeps the architecture simple for
  Stage 1, per MIP section 6's general "keep infrastructure complexity proportional to actual
  need each stage" principle.
- Loss: MSE between predicted and actual next-state delta, on real (unlabeled, presumed-clean)
  training windows. This is the self-supervised training objective from the strategy note.

### `ml/models/bilstm/infer.py`
- Given a trained model and a window, returns the per-timestep prediction error (the score the
  rest of the pipeline consumes). This is the function that populates
  `AISObservation.prediction_error` on the `"injected_synthetic"` dataset after training, per
  Section 2's note above.

### `ml/training/train.py`
- Pulls clean windows via `ml/features/pipeline.py` (the self-supervised training set — do not
  route injected/synthetic data into training, only into evaluation, or the model learns to
  predict the injected anomalies as normal).
- Splits by vessel (not by window) into train/val, so no vessel's windows leak across the split.
- Trains `ml/models/bilstm/model.py`, logs every run to MLflow: hyperparameters (window length,
  hidden size, learning rate, epochs), the training/val loss curve, and — critically — the
  precision/recall/F1 from running `ml/evaluation/harness.py`'s `evaluate()` against the
  `"injected_synthetic"` dataset (Section 2) using this run's trained model to fill in
  `prediction_error` (Section 3's `infer.py`). Every MLflow run should end with a number
  comparable to `baselines.py::speed_jump_detector`'s harness output — that baseline is the floor
  a real trained model needs to beat.
- CLI entrypoint mirroring `ml/evaluation/harness.py`'s style (`argparse`, run from `cd ml`).

### `ml/models/bilstm/tests/`
A CPU-fast smoke test: tiny synthetic data (a handful of windows, not a real DB), 1-2 epochs,
asserting the training loop runs end to end and loss decreases. This is a CI test (no real
database, no GPU, no MLflow server required — point `MLFLOW_TRACKING_URI` at a local temp dir for
the test only), not a training-quality test.

---

## 4. `agent/` — the investigation agent

Currently just READMEs. Build a plain bounded state machine, not LangGraph — MIP section 6
explicitly allows this ("or a custom bounded state machine if LangGraph feels heavier than
needed"), and a 3-tool, 4-hypothesis agent is exactly the case where LangGraph's overhead buys
nothing yet. Revisit LangGraph only if a later stage's state space genuinely outgrows a plain
state machine.

### `agent/orchestrator/state_machine.py`
States, matching HLD section 4.1's sequence diagram exactly:
`RECEIVED -> GATHERING_EVIDENCE -> HYPOTHESIZING -> {REPORTING | ESCALATING} -> DONE`.

- Entry point: a flagged anomaly `(mmsi, flagged_at, anomaly_score, anomaly_type)` from the
  scoring service (Section 3's `infer.py`, wrapped by whatever calls it on a schedule or stream —
  that scheduling wrapper is not in scope for this manifesto; stub it as a function call for now).
- `GATHERING_EVIDENCE`: calls all three tools (Section 4 below) and collects results.
- `HYPOTHESIZING`: forms a hypothesis in `{jamming, targeted_spoof, equipment_fault, benign}`
  with a confidence score. For Stage 1, a rule-assisted heuristic is acceptable and matches
  Sem5IP.md's own framing ("rule-assisted investigation agent") — e.g. zone match from
  `jamming_zones` tool pushes toward `jamming`, isolated anomaly with no zone match and no similar
  past incident pushes toward `targeted_spoof`, low anomaly score relative to threshold pushes
  toward `equipment_fault` or `benign`. Do not build an ML classifier for this step in Stage 1;
  that's exactly the kind of component the data strategy note defers (see its section on Laya:
  a typed-decision classifier is the natural fit here once real incident history exists to fine-
  tune one against — not before).
- Confidence threshold for the `REPORTING` vs `ESCALATING` branch: pick an explicit starting
  number (e.g. 0.7) and write it as a named constant with a comment marking it as an
  uncalibrated Stage 1 placeholder, since MIP section 8.4 already schedules a dedicated
  calibration pass for Stage 3 — don't try to calibrate it properly now, that's premature given
  no real incident history exists yet to calibrate against.
- Every tool call and its result gets appended to a `tool_call_log` list as it happens, written
  into `incidents.tool_call_log` (the JSONB column added in `backend/models/schema.sql`) when the
  incident row is created/updated — this is the auditability requirement from MIP section 4.2,
  and the schema column exists specifically for this.
- Write the row to `incidents` (mmsi, flagged_at, window bounds, flagged_position, anomaly_score,
  anomaly_type, hypothesis, confidence, status, evidence, tool_call_log) at the end of the run,
  regardless of which branch it took.

### `agent/tools/track_history.py`
Queries `vessel_position` for a vessel's recent + full trajectory around the flagged window.
Read-only, `asyncpg`, mirrors the query style already established in `ingestion/storage.py`.

### `agent/tools/jamming_zones.py`
Checks the flagged position/time against `jamming_zones` (the table added in
`backend/models/schema.sql`) using a PostGIS spatial query (`ST_Contains` /
`ST_DWithin` against the `zone` geography column). Returns zone match yes/no plus which zone and
its stored confidence, per HLD section 4.1's sequence diagram.

### `agent/tools/incident_history.py`
Queries `incidents` for similar past incidents — same `mmsi`, or same `anomaly_type` /
`hypothesis` within some geographic and time proximity of the current flagged position. Expect
this to return little or nothing in early Stage 1 runs (the table starts empty); that's expected,
not a bug, and the state machine's `HYPOTHESIZING` step should treat "no similar history found" as
a neutral signal, not a rule-out.

### `agent/report_generator/report.py`
Only called on the `REPORTING` branch (high confidence). Takes the incident's collected evidence
and calls the Claude API (Sonnet-class, per MIP section 6) to draft a structured incident report:
vessel, time, anomaly type, score, the hypothesis, and the supporting evidence from each tool,
written in prose. Store the result in `incidents.report_text`. This is the one place in this
manifesto that should call an LLM — everything upstream of it (hypothesis formation) is rule-
based per the decision above.

### `agent/tests/`
Unit tests for the state machine with all three tools mocked (fixed canned responses), asserting:
correct branch taken at each confidence level, `tool_call_log` populated correctly, and an
`incidents` row shape that matches the schema. No real DB or Claude API call in CI; a separate
manual/local integration test (documented in `agent/README.md`, not written here) can exercise
the real path once Sections 1-3 have produced a real trained model to flag anomalies with.

---

## 5. Explicit non-goals (leave alone)

Do not touch in this pass, all correctly out of scope per Sem5IP.md section 2 and the MIP's own
stage boundaries:

- `ml/models/clustering/` (DBSCAN) — Stage 2.
- Transformer model migration — Stage 2.
- Automated jamming-zone ingestion — Stage 2 (Stage 1's `jamming_zones` rows are seeded manually;
  `source = 'manual'` in the schema already anticipates the Stage 2 switch).
- LangGraph — only revisit if the plain state machine above genuinely can't express a later
  stage's requirements.
- Laya/Jev integration into the hypothesis step — deferred until real incident history exists to
  fine-tune against (see `docs/research_notes/ml-data-strategy.md`).
- `backend/api/` (FastAPI endpoints) and `frontend/dashboard/` — not in this manifesto's scope;
  they read from `incidents` and `vessel_position` once this work lands, but building them is a
  separate pass.
- Multi-tenant auth, Terraform — Stage 3.

---

## 6. Definition of done for this manifesto

- [ ] `ml/features/extract.py`, `pipeline.py`, `inject.py` written and unit-tested (no real DB
      needed for the unit tests, fixtures only).
- [ ] `ml/requirements.txt` and `ml/requirements-dev.txt` added.
- [ ] `ml/models/bilstm/model.py`, `infer.py`, `ml/training/train.py` written; a real training run
      completed against real ingested data (once the 14-day / 20-report floor is met) with a
      result logged in MLflow beating `baselines.py::speed_jump_detector` on the
      `"injected_synthetic"` harness run.
- [ ] `"injected_synthetic"` registered in `ml/evaluation/datasets.py::DATASET_LOADERS`.
- [ ] `agent/orchestrator/state_machine.py`, all three `agent/tools/*.py`, and
      `agent/report_generator/report.py` written, with `agent/tests/` passing against mocked
      tools.
- [ ] A real end-to-end run exists: one real flagged anomaly, taken all the way through to either
      a written `incidents.report_text` or a logged escalation, with `tool_call_log` populated.
- [ ] Every new Python module has a corresponding `.github/workflows/*.yml` CI entry, matching the
      existing `ingestion-tests.yml` / `ml-evaluation-tests.yml` pattern (path-filtered, `pytest`
      only, no real DB/GPU/API key required in CI).
