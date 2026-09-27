"""Score a trained BiLSTM checkpoint against a held-out injected-synthetic eval set.

This is the wire nothing else in the repo has ever made: it connects
ml/models/bilstm/infer.py::prediction_errors() (the scoring function) to
ml/evaluation/harness.py::evaluate() (the precision/recall/F1 harness). See
ImplementationPlans/Sem5_Evaluation_And_Threshold.md for the full brief, including why
this deliberately stops short of a live scoring service, agent wiring, or Laya
integration.

This never goes through harness.py's own CLI (main()): that CLI calls every
registered dataset loader as `load(path)`, which matches the synchronous file
loaders but not `load_injected_synthetic`'s async, multi-argument signature (see
harness.py::main and section 1 of the implementation plan). This script does its own
async data loading and model inference in front of `evaluate()`, and never touches
harness.py's CLI wrapper; `--dataset gps_spoofing_mass` there keeps working exactly
as before.

Usage:
    cd ml
    python -m evaluation.score_checkpoint \\
        --dsn postgresql://ghast:ghast@localhost:5432/ghast \\
        --checkpoint checkpoints/epoch_010.pt \\
        --eval-start 2026-05-01 --eval-end 2026-05-16 \\
        --seed 0

Pick --eval-start/--eval-end so the range is disjoint from whatever range trained the
checkpoint (the historical run used 2026-04-01..2026-04-16 - use a different April
window, or a live window, so the eval set isn't windows the model already saw).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import torch

from evaluation.baselines import Detector, prediction_error_detector, speed_jump_detector
from evaluation.datasets import AISObservation
from evaluation.harness import EvaluationResult, evaluate
from features.extract import COG_INDEX, HEADING_INDEX, MISSING_VALUE, N_FEATURES, SOG_INDEX
from features.inject import InjectedWindow, build_synthetic_dataset
from features.pipeline import stream_feature_windows
from models.bilstm.infer import prediction_errors
from models.bilstm.model import BiLSTMNextDelta

DEFAULT_SEED = 0
DEFAULT_MLFLOW_TRACKING_URI = "file:mlruns"
DEFAULT_EXPERIMENT_NAME = "bilstm-checkpoint-scoring"
CONTROL_LABEL = "control"  # display name for InjectedWindow.pattern is None
THRESHOLD_SWEEP_POINTS = 25


def load_checkpoint(checkpoint_path: Path, device: torch.device) -> tuple[BiLSTMNextDelta, dict]:
    """Reconstruct the model architecture and load trained weights onto it.

    Only `model_state` is used for scoring; `optimiser_state` is training-only and
    the epoch/loss fields are kept just to log as provenance later.
    """
    checkpoint = torch.load(checkpoint_path, map_location=device)
    model = BiLSTMNextDelta(N_FEATURES).to(device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    return model, checkpoint


def _implied_acceleration(features: np.ndarray) -> list[float | None]:
    """Per-timestep change in reported SOG - our own stand-in for the
    acceleration/speed_calc columns the gps_spoofing_mass CSV ships pre-computed.

    Nothing in the injected-synthetic pipeline computes this, but
    speed_jump_detector (the existing baseline) needs some acceleration signal to
    score on, so this gives it one on this dataset too. Not a label; purely a
    derived feature for the baseline detector.
    """
    accelerations: list[float | None] = [None]
    for index in range(1, len(features)):
        previous_sog, current_sog = features[index - 1, SOG_INDEX], features[index, SOG_INDEX]
        if previous_sog == MISSING_VALUE or current_sog == MISSING_VALUE:
            accelerations.append(None)
        else:
            accelerations.append(float(current_sog - previous_sog))
    return accelerations


def score_injected_windows(
    model: BiLSTMNextDelta, injected_windows: list[InjectedWindow]
) -> list[AISObservation]:
    """Flatten scored windows into AISObservation rows.

    Mirrors datasets.py::load_injected_synthetic's flattening shape, but sets
    `prediction_error` to this model's real scored value (instead of leaving it
    unset) and carries `injected.pattern` through so later reporting can break
    results out by spoof pattern without re-deriving it from window structure.
    """
    observations: list[AISObservation] = []
    for injected in injected_windows:
        window = injected.window
        errors = prediction_errors(model, window)
        accelerations = _implied_acceleration(window.features)
        for index, is_spoofed in enumerate(injected.is_spoofed):
            row_features = window.features[index]
            latitude, longitude = window.positions[index]
            observations.append(AISObservation(
                mmsi=str(window.mmsi),
                timestamp=window.timestamps[index].isoformat(),
                latitude=float(latitude),
                longitude=float(longitude),
                sog=float(row_features[SOG_INDEX]),
                cog=float(row_features[COG_INDEX]),
                heading=float(row_features[HEADING_INDEX]),
                is_spoofed=is_spoofed,
                prediction_error=float(errors[index]),
                acceleration=accelerations[index],
                pattern=injected.pattern,
                source="injected_synthetic_scored",
            ))
    return observations


def threshold_candidates(scores: list[float], n: int = THRESHOLD_SWEEP_POINTS) -> list[float]:
    """Percentile-based sweep over the observed score distribution.

    A trained model's prediction-error scale isn't knowable in advance, unlike a
    hand-picked constant - so thresholds come from the data itself. Falls back to
    a single 0.0 threshold on a degenerate all-zero distribution (e.g. a baseline
    detector with no usable signal on this dataset) rather than erroring.
    """
    positive_scores = [score for score in scores if score > 0]
    if not positive_scores:
        return [0.0]
    percentiles = np.linspace(1, 99.5, n)
    candidates = sorted({float(np.percentile(positive_scores, p)) for p in percentiles})
    return candidates or [0.0]


@dataclass(frozen=True)
class ThresholdSweep:
    detector_name: str
    results: list[EvaluationResult]

    def best_by_f1(self) -> EvaluationResult:
        return max(self.results, key=lambda result: result.metrics.f1)


def sweep_thresholds(
    observations: list[AISObservation], detector_name: str, detector: Detector, thresholds: list[float]
) -> ThresholdSweep:
    return ThresholdSweep(detector_name, [evaluate(observations, detector, threshold) for threshold in thresholds])


def per_pattern_breakdown(
    observations: list[AISObservation], detector: Detector, threshold: float
) -> dict[str, EvaluationResult]:
    """Precision/recall/F1 broken out per spoof pattern, plus a "control" group of
    unmodified windows, so an aggregate F1 can't hide a bad false-positive rate on
    ordinary, unremarkable maneuvers (see the implementation plan, section 2.5).
    """
    grouped: dict[str, list[AISObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.pattern or CONTROL_LABEL, []).append(observation)
    return {pattern: evaluate(group, detector, threshold) for pattern, group in grouped.items()}


def _print_sweep(sweep: ThresholdSweep) -> None:
    print(f"--- {sweep.detector_name} threshold sweep ({len(sweep.results)} thresholds) ---")
    for result in sweep.results:
        m = result.metrics
        print(f"  threshold={result.threshold:.6f}  precision={m.precision:.3f}  recall={m.recall:.3f}  f1={m.f1:.3f}")


def _print_breakdown(breakdown: dict[str, EvaluationResult], threshold: float) -> None:
    print(f"--- per-pattern breakdown @ threshold={threshold:.6f} ---")
    for pattern, result in sorted(breakdown.items()):
        m = result.metrics
        print(
            f"  {pattern:<24} n={result.n_observations:<6} precision={m.precision:.3f}  recall={m.recall:.3f}  "
            f"f1={m.f1:.3f}  fp={m.confusion.false_positive}  tp={m.confusion.true_positive}"
        )


def _log_mlflow(
    checkpoint_path: Path,
    checkpoint: dict,
    eval_start: datetime,
    eval_end: datetime,
    seed: int,
    sweeps: dict[str, ThresholdSweep],
    chosen: EvaluationResult,
    baseline_best: EvaluationResult,
    breakdown: dict[str, EvaluationResult],
) -> None:
    """One MLflow run per scoring pass: checkpoint identity, eval range, the full
    sweep per detector, the chosen threshold, and the per-pattern breakdown.
    """
    import mlflow

    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", DEFAULT_MLFLOW_TRACKING_URI))
    mlflow.set_experiment(DEFAULT_EXPERIMENT_NAME)
    with mlflow.start_run():
        mlflow.log_param("checkpoint_path", str(checkpoint_path))
        mlflow.log_param("checkpoint_epoch", checkpoint.get("epoch"))
        mlflow.log_param("checkpoint_train_loss", checkpoint.get("train_loss"))
        mlflow.log_param("checkpoint_val_loss", checkpoint.get("val_loss"))
        mlflow.log_param("eval_start", eval_start.isoformat())
        mlflow.log_param("eval_end", eval_end.isoformat())
        mlflow.log_param("seed", seed)

        mlflow.log_metric("chosen_threshold", chosen.threshold)
        mlflow.log_metric("chosen_precision", chosen.metrics.precision)
        mlflow.log_metric("chosen_recall", chosen.metrics.recall)
        mlflow.log_metric("chosen_f1", chosen.metrics.f1)
        mlflow.log_metric("baseline_best_f1", baseline_best.metrics.f1)
        mlflow.log_metric("beats_baseline", float(chosen.metrics.f1 > baseline_best.metrics.f1))

        mlflow.log_dict(
            {
                name: [
                    {"threshold": r.threshold, "precision": r.metrics.precision, "recall": r.metrics.recall, "f1": r.metrics.f1}
                    for r in sweep.results
                ]
                for name, sweep in sweeps.items()
            },
            "threshold_sweep.json",
        )
        mlflow.log_dict(
            {
                pattern: {
                    "n": result.n_observations,
                    "precision": result.metrics.precision,
                    "recall": result.metrics.recall,
                    "f1": result.metrics.f1,
                    "true_positive": result.metrics.confusion.true_positive,
                    "false_positive": result.metrics.confusion.false_positive,
                }
                for pattern, result in breakdown.items()
            },
            "per_pattern_breakdown.json",
        )


async def run(
    dsn: str,
    checkpoint_path: Path,
    eval_start: datetime,
    eval_end: datetime,
    seed: int,
    device_name: str | None,
    skip_mlflow: bool,
) -> int:
    device = torch.device(device_name) if device_name else torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, checkpoint = load_checkpoint(checkpoint_path, device)
    print(
        f"scoring checkpoint={checkpoint_path} epoch={checkpoint.get('epoch')} "
        f"train_loss={checkpoint.get('train_loss')} val_loss={checkpoint.get('val_loss')}"
    )
    print(f"eval range: {eval_start.isoformat()}..{eval_end.isoformat()} (seed={seed}, device={device})")

    windows = [
        window
        async for window in stream_feature_windows(
            dsn,
            eval_start,
            eval_end,
            source="historical",
            max_windows=1000,
            progress_every=50_000,
            on_progress=lambda rows, vessels, windows: print(
                f"loading: {rows:,} rows | {vessels:,} vessels | {windows:,} windows",
                flush=True,
            ),
        )
    ]
    if not windows:
        print(f"no clean windows found for {eval_start.isoformat()}..{eval_end.isoformat()}", file=sys.stderr)
        return 1
    injected_windows = build_synthetic_dataset(windows, seed=seed)
    observations = score_injected_windows(model, injected_windows)
    print(f"scored {len(observations)} observations across {len(injected_windows)} windows")

    sweeps = {
        "prediction_error": sweep_thresholds(
            observations, "prediction_error", prediction_error_detector,
            threshold_candidates([prediction_error_detector(o) for o in observations]),
        ),
        "speed_jump": sweep_thresholds(
            observations, "speed_jump", speed_jump_detector,
            threshold_candidates([speed_jump_detector(o) for o in observations]),
        ),
    }
    for sweep in sweeps.values():
        _print_sweep(sweep)

    chosen = sweeps["prediction_error"].best_by_f1()
    baseline_best = sweeps["speed_jump"].best_by_f1()

    breakdown = per_pattern_breakdown(observations, prediction_error_detector, chosen.threshold)
    _print_breakdown(breakdown, chosen.threshold)

    if chosen.metrics.f1 > baseline_best.metrics.f1:
        verdict = "beats"
    elif chosen.metrics.f1 < baseline_best.metrics.f1:
        verdict = "does NOT beat"
    else:
        verdict = "ties"
    print(
        f"\nVerdict: prediction_error_detector (F1={chosen.metrics.f1:.3f} @ threshold={chosen.threshold:.6f}) "
        f"{verdict} speed_jump_detector (F1={baseline_best.metrics.f1:.3f} @ threshold={baseline_best.threshold:.6f})."
    )
    if verdict == "does NOT beat":
        print(
            "This is a valid outcome to report, not a failure to hide: a live scoring service built on "
            "this checkpoint should ship with an explicit caveat that detection quality is unproven."
        )

    if not skip_mlflow:
        _log_mlflow(checkpoint_path, checkpoint, eval_start, eval_end, seed, sweeps, chosen, baseline_best, breakdown)

    print(
        f"\nIf this is the run you want to ship, update OPERATING_THRESHOLD in "
        f"models/bilstm/threshold.py to {chosen.threshold:.6f} and fill in its provenance comment."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--checkpoint", required=True, help="Path to a .pt checkpoint, e.g. checkpoints/epoch_010.pt")
    parser.add_argument("--eval-start", required=True, help="ISO date/datetime, start of the eval range")
    parser.add_argument("--eval-end", required=True, help="ISO date/datetime, end of the eval range")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--device", choices=("cpu", "cuda"), help="Defaults to cuda if available, else cpu.")
    parser.add_argument("--no-mlflow", action="store_true", help="Skip MLflow logging (useful for quick local checks).")
    args = parser.parse_args(argv)

    return asyncio.run(run(
        args.dsn,
        Path(args.checkpoint),
        datetime.fromisoformat(args.eval_start),
        datetime.fromisoformat(args.eval_end),
        args.seed,
        args.device,
        args.no_mlflow,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
