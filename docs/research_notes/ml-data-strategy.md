# ML Data Strategy for the Bi-LSTM Detector (Stage 1)

Status: accepted
Applies to: `ml/features/`, `ml/models/bilstm/`, `ml/training/`, `ml/evaluation/`
Intended path in repo: `docs/research_notes/ml-data-strategy.md`
Supersedes nothing; extends MIP section 5 and Sem5IP.md section 4 with a decision those documents leave open.

---

## 1. The problem

The HLD (section 3.1) specifies the Bi-LSTM as a sequence model: given a vessel's recent track,
predict its next plausible state, and flag a large prediction-vs-reported deviation as the
spoofing signal. That requires real per-vessel sequences to train and evaluate against.

`data/research_datasets/gps_spoofing_mass/` (loaded by
`ml/evaluation/datasets.py::load_gps_spoofing_mass`) is 6,351 labeled observations across 4,892
MMSIs, about 1.3 observations per vessel on average. Its own README already flags this
("point-level ground truth, not sequence data"). Most vessels in it have exactly one row. You
cannot train a sequence model on it, and for most of its rows there is no preceding track to
condition a trained model's inference on either.

The second dataset the HLD wants (Pohontu et al., real-vs-simulated spoofed AIS tracks,
DOI 10.33436/v35i1y202503) would fix this directly, but `data/research_datasets/README.md`
already notes no public download has been found for it. Waiting on that before starting Weeks
7-10 blocks the whole ML track on something outside the team's control.

## 2. Decision

Train and evaluate the Bi-LSTM primarily on the project's own live ingestion data, not on
`gps_spoofing_mass`. Concretely:

1. **Training data: self-supervised, from `vessel_position`.** Next-state prediction needs no
   labels — real AIS traffic is overwhelmingly normal, so a large sample of real per-vessel
   tracks from the running ingestion pipeline is a valid unlabeled training set as-is. This also
   means training data quality improves automatically the longer ingestion runs, with no manual
   labeling effort.

2. **Evaluation ground truth: synthetic spoof injection on real tracks.** Take real, clean
   per-vessel trajectories pulled from `vessel_position`, copy them, and inject synthetic
   spoofing patterns into the copies. This is a standard technique in the spoofing-detection
   literature precisely because real, consented, ground-truth-labeled spoofing incidents at
   scale don't exist publicly. It gives full control over sequence density, spoof severity, and
   spoof type, none of which `gps_spoofing_mass` can offer.

3. **`gps_spoofing_mass` demoted to a secondary sanity check.** Keep using it exactly as
   `ml/evaluation/harness.py` and `ml/evaluation/baselines.py` already do today — it's fine for
   confirming a prediction-error-shaped signal correlates with the dataset's own labels at the
   point level. It stops being the dataset the Bi-LSTM is trained or primarily benchmarked
   against.

4. **Keep chasing Pohontu et al. in parallel, but don't block on it.** If the data becomes
   available later, it slots into `ml/evaluation/datasets.py`'s `DATASET_LOADERS` registry as a
   second real-data sanity check alongside `gps_spoofing_mass`, same pattern as today.

## 3. Injected spoof patterns (what the injector must produce)

Each pattern takes a real clean trajectory (sequence of position reports for one MMSI) and
returns a modified copy plus a label per point (`is_spoofed: bool`). Grounded in the physical-
limits framing from HLD section 3.1 ("large deviation ... beyond what the vessel's real physical
limits allow"):

| Pattern | What it does | Physical signal violated |
|---|---|---|
| Teleport jump | Displaces one or more points by a large offset (km-scale) with no corresponding transit time | Implied speed far exceeds vessel-class max speed |
| Gradual drift | Adds a slowly-growing positional bias over N points, small enough per-step to stay under a naive per-point threshold | Cumulative deviation from a physically consistent heading/speed-integrated path |
| Freeze / replay | Repeats a past position (or a short window of past positions) instead of the true next one | Zero or looping displacement inconsistent with reported SOG > 0 |
| Impossible kinematics | Perturbs COG/heading/rate-of-turn to values inconsistent with the vessel's reported speed and class | Rate-of-turn or heading change exceeds vessel-class turning-radius limits |

Each pattern needs: a severity parameter (so the eval set spans easy/medium/hard cases, not just
maximally obvious spoofing), and a fixed random seed for reproducibility, matching the
reproducibility bar the evaluation harness already sets (`harness.py`'s whole point is that every
model iteration has a comparable number attached).

## 4. How this fits the existing eval harness without rewriting it

`ml/evaluation/harness.py::evaluate()` takes `list[AISObservation]` and a `Detector` (a callable
`AISObservation -> float`, thresholded into a prediction). Do not change this signature or
`harness.py` itself. Instead:

- Flatten each injected synthetic trajectory back into point-level `AISObservation` rows (one per
  timestep, `is_spoofed` carried from the injector's per-point label), matching the existing
  dataclass shape.
- Run the trained Bi-LSTM once per trajectory to compute each point's next-state prediction
  error, and populate `AISObservation.prediction_error` with it (this field already exists in the
  dataclass and is already what `baselines.py::prediction_error_detector` reads).
- The existing `prediction_error_detector` then works unchanged on the synthetic set, exactly as
  it does today on `gps_spoofing_mass`. This is deliberate: the harness, the `Detector` protocol,
  and the metrics code (`metrics.py`) stay exactly as-is; only a new dataset loader and a
  model-inference step are added.

Register the new loader in `ml/evaluation/datasets.py::DATASET_LOADERS` as
`"injected_synthetic"`, alongside `"gps_spoofing_mass"`.

## 5. Data volume gate before training starts

Self-supervised training needs enough real per-vessel history to form meaningful sequences.
Don't start Bi-LSTM training until ingestion has accumulated, as a floor:

- At least 14 days of continuous ingestion (`ingestion/main.py` running via
  `infra/docker/docker-compose.yml`'s `ingestion` service).
- A minimum per-vessel sequence length usable for training: vessels with fewer than ~20 position
  reports across that window are excluded from the training set (too sparse to form a
  meaningful trajectory window), not padded or interpolated to fake density.

If the 14-day mark arrives short on usable vessels (bounding box was too narrow, feed too quiet),
widen `AISSTREAM_BOUNDING_BOXES` (see `ingestion/collector/config.py`) before writing model code,
rather than compensating for sparse data in the model itself.

## 6. Explicit limitations (Stage 1, accepted)

- Synthetic injected spoofing is a proxy for real jamming/spoofing physics, not a replacement for
  real incident data. This is the same category of risk the HLD's own risk table (section 9)
  already accepts for "no data access to real vessels" — it's covered by the existing mitigation
  ("public feeds + public datasets cover Stage 1 entirely"), just made explicit for the model
  specifically.
- The self-generated incident history (HLD section 5, "our own accumulating incident history")
  becomes the real long-term ground truth once the agent (`agent/`) is live and producing
  reviewed incidents. Until then, injected synthetic data and the `gps_spoofing_mass` sanity
  check are what Stage 1 has.
