"""Score a trained BiLSTM checkpoint on real, unlabeled AIS windows from the database.

There is no synthetic spoofing here. Windows come straight from `vessel_position`
(live or historical rows), the checkpoint scores every report, and the script prints
what the detectors do on that real traffic:

* the prediction-error distribution (percentiles),
* how often each detector votes, and how often two or more agree,
* the flag rate at the current `OPERATING_THRESHOLD`,
* the threshold that would give a chosen flag rate (calibration by alert budget),
* the same flag rates split into underway and stationary windows.

Real data has no ground-truth labels, so this reports rates, not precision, recall or
F1. On traffic where real spoofing is rare, the flag rate is an upper bound on the false
positive rate. It is not a detection score. Detection quality only becomes measurable
once analyst-reviewed incidents exist (ImplementationPlans/01_Trust_Pass.md). The one
labeled dataset in the repo is the public gps_spoofing_mass file, scored through
`evaluation.harness`.

Usage:
    cd ml
    python -m evaluation.score_checkpoint \\
        --dsn postgresql://ghast:ghast@localhost:5432/ghast \\
        --checkpoint checkpoints/epoch_010.pt \\
        --source historical --eval-start 2026-04-01 --eval-end 2026-04-16 \\
        --out reports/historical_epoch_010.json

Run it once per source (`--source historical`, then `--source live`) and compare the two
reports: a threshold chosen on one source but flagging very differently on the other is
the drift problem in docs/ml-pipeline.md, now visible as numbers.

By default only validation vessels (the hash split training used) are scored, so the
model has not trained on them. Pass `--include-training-vessels` to score everything.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import torch

from features.extract import (
    FREEZE_DISPLACEMENT_EPSILON_KNOTS, IMPLIED_SPEED_INDEX, MISSING_VALUE, N_FEATURES, SOG_INDEX,
)
from features.pipeline import FeatureWindow, TrainingDataSource, stream_feature_windows
from models.bilstm.infer import prediction_errors_batch
from models.bilstm.model import BiLSTMNextDelta
from models.bilstm.threshold import OPERATING_THRESHOLD
from training.dataset_cache import is_validation_vessel

DEFAULT_MLFLOW_TRACKING_URI = "sqlite:///mlruns/mlflow.db"
DEFAULT_EXPERIMENT_NAME = "bilstm-real-data-scoring"
DEFAULT_EVAL_BATCH_SIZE = 1024
PROFILE_EVERY_BATCHES = 10
HOLDOUT_METHOD = "validation_vessel_split (fraction=0.2)"
ERROR_PERCENTILES = (50.0, 90.0, 95.0, 99.0, 99.9)
TARGET_FLAG_RATES = (0.05, 0.01, 0.005, 0.001)
# Same defaults the live scorer uses for its rule detectors (scoring/live_scorer.py).
DEFAULT_FREEZE_REPLAY_THRESHOLD = 0.5
UNDERWAY_KNOTS = 1.0  # a window is "underway" if any report implies more than this speed


def load_checkpoint(checkpoint_path: Path, device: torch.device) -> tuple[BiLSTMNextDelta, dict]:
    """Reconstruct the model architecture and load trained weights onto it."""
    checkpoint = torch.load(checkpoint_path, map_location=device)
    model = BiLSTMNextDelta(N_FEATURES).to(device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    return model, checkpoint


@dataclass(frozen=True)
class ReportScores:
    """Flat per-report arrays for reports 1..19 of every scored window (report 0 has no error)."""

    errors: np.ndarray            # BiLSTM prediction error, degrees
    sog: np.ndarray               # reported speed, NaN if missing
    implied_speed: np.ndarray     # position-implied speed, NaN if missing
    speed_change: np.ndarray      # |SOG - previous SOG|, NaN if either is missing
    underway: np.ndarray          # bool, window-level: any report implies more than UNDERWAY_KNOTS
    n_windows: int

    @property
    def n_reports(self) -> int:
        return int(self.errors.size)


def _nan_missing(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.float64)
    values[values == MISSING_VALUE] = np.nan
    return values


def score_windows(model: BiLSTMNextDelta, windows: list[FeatureWindow]) -> ReportScores:
    """Run the model on real windows and flatten to per-report arrays. No labels, no injection."""
    errors_per_window = prediction_errors_batch(model, windows)
    errors, sog, implied, change, underway = [], [], [], [], []
    for window, window_errors in zip(windows, errors_per_window, strict=True):
        features = window.features
        window_sog = _nan_missing(features[:, SOG_INDEX])
        window_implied = _nan_missing(features[:, IMPLIED_SPEED_INDEX])
        errors.append(np.asarray(window_errors, dtype=np.float64)[1:])
        sog.append(window_sog[1:])
        implied.append(window_implied[1:])
        change.append(np.abs(window_sog[1:] - window_sog[:-1]))
        moving = bool(np.nanmax(window_implied, initial=0.0) > UNDERWAY_KNOTS)
        underway.append(np.full(len(window_sog) - 1, moving))
    return ReportScores(
        np.concatenate(errors), np.concatenate(sog), np.concatenate(implied),
        np.concatenate(change), np.concatenate(underway), len(windows),
    )


def merge_scores(parts: list[ReportScores]) -> ReportScores:
    return ReportScores(
        np.concatenate([p.errors for p in parts]), np.concatenate([p.sog for p in parts]),
        np.concatenate([p.implied_speed for p in parts]), np.concatenate([p.speed_change for p in parts]),
        np.concatenate([p.underway for p in parts]), sum(p.n_windows for p in parts),
    )


def freeze_replay_scores(sog: np.ndarray, implied_speed: np.ndarray) -> np.ndarray:
    """Vectorised `evaluation.baselines.freeze_replay_detector`: reported speed minus
    implied speed, only where the position implies (almost) no motion."""
    valid = ~np.isnan(sog) & ~np.isnan(implied_speed) & (implied_speed >= 0) & (implied_speed <= FREEZE_DISPLACEMENT_EPSILON_KNOTS)
    return np.where(valid, np.maximum(0.0, np.nan_to_num(sog) - np.nan_to_num(implied_speed)), 0.0)


def speed_jump_scores(speed_change: np.ndarray) -> np.ndarray:
    """Vectorised `evaluation.baselines.speed_jump_detector` over |SOG change|."""
    return np.nan_to_num(speed_change, nan=0.0)


def flag_rate(scores: np.ndarray, threshold: float) -> float:
    return float(np.mean(scores > threshold)) if scores.size else 0.0


def threshold_for_flag_rate(errors: np.ndarray, target_rate: float) -> float:
    """The prediction-error threshold above which about `target_rate` of reports are flagged."""
    if not 0.0 < target_rate < 1.0:
        raise ValueError("target_rate must be between 0 and 1 (exclusive)")
    if errors.size == 0:
        raise ValueError("no scores to calibrate on")
    return float(np.quantile(errors, 1.0 - target_rate))


def build_report(
    scores: ReportScores,
    operating_threshold: float | None,
    freeze_threshold: float = DEFAULT_FREEZE_REPLAY_THRESHOLD,
    speed_jump_threshold: float | None = None,
    target_flag_rates: tuple[float, ...] = TARGET_FLAG_RATES,
) -> dict:
    """Everything worth knowing about detector behaviour on this real traffic, as plain data."""
    if scores.n_reports == 0:
        raise ValueError("no scored reports")
    freeze = freeze_replay_scores(scores.sog, scores.implied_speed)
    votes = {}
    vote_matrix = []
    if operating_threshold is not None:
        vote_matrix.append(scores.errors > operating_threshold)
        votes["prediction_error"] = flag_rate(scores.errors, operating_threshold)
    vote_matrix.append(freeze > freeze_threshold)
    votes["freeze_replay"] = float(np.mean(vote_matrix[-1]))
    if speed_jump_threshold is not None:
        vote_matrix.append(speed_jump_scores(scores.speed_change) > speed_jump_threshold)
        votes["speed_jump"] = float(np.mean(vote_matrix[-1]))
    vote_count = np.sum(np.vstack(vote_matrix), axis=0)

    by_motion = {}
    for name, mask in (("underway", scores.underway), ("stationary", ~scores.underway)):
        by_motion[name] = {
            "reports": int(mask.sum()),
            "prediction_error_flag_rate": flag_rate(scores.errors[mask], operating_threshold) if operating_threshold is not None and mask.any() else None,
        }
    return {
        "windows": scores.n_windows,
        "reports": scores.n_reports,
        "error_percentiles": {f"p{p:g}": float(np.percentile(scores.errors, p)) for p in ERROR_PERCENTILES},
        "operating_threshold": operating_threshold,
        "flag_rate_at_operating_threshold": votes.get("prediction_error"),
        "detector_vote_rates": votes,
        "any_detector_flag_rate": float(np.mean(vote_count >= 1)),
        "two_or_more_detectors_flag_rate": float(np.mean(vote_count >= 2)),
        "by_motion": by_motion,
        "threshold_for_flag_rate": {f"{rate:g}": threshold_for_flag_rate(scores.errors, rate) for rate in target_flag_rates},
        "note": "Rates on real, unlabeled traffic. Where real spoofing is rare a flag rate is an upper bound on the false positive rate, not a detection score.",
    }


def print_report(report: dict) -> None:
    print(f"\nscored {report['reports']:,} reports in {report['windows']:,} windows")
    print("prediction error percentiles (degrees):")
    for name, value in report["error_percentiles"].items():
        print(f"  {name:<6} {value:.6f}")
    print("detector vote rates (share of reports):")
    for name, value in report["detector_vote_rates"].items():
        print(f"  {name:<18} {value:.4%}")
    print(f"  any detector       {report['any_detector_flag_rate']:.4%}")
    print(f"  two or more agree  {report['two_or_more_detectors_flag_rate']:.4%}")
    if report["operating_threshold"] is not None:
        print(f"prediction-error flag rate at OPERATING_THRESHOLD={report['operating_threshold']}: {report['flag_rate_at_operating_threshold']:.4%}")
        for name, row in report["by_motion"].items():
            rate = row["prediction_error_flag_rate"]
            print(f"  {name:<11} reports={row['reports']:>10,}  flag rate={'n/a' if rate is None else f'{rate:.4%}'}")
    print("threshold that gives a target flag rate:")
    for rate, threshold in report["threshold_for_flag_rate"].items():
        print(f"  flag rate {float(rate):.3%} -> threshold {threshold:.6f}")
    print(f"\n{report['note']}")


def _log_mlflow(report: dict, checkpoint_path: Path, checkpoint: dict, params: dict) -> None:
    import mlflow

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", DEFAULT_MLFLOW_TRACKING_URI)
    if tracking_uri == DEFAULT_MLFLOW_TRACKING_URI:
        Path("mlruns").mkdir(exist_ok=True)  # SQLAlchemy will not create the parent directory
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(DEFAULT_EXPERIMENT_NAME)
    with mlflow.start_run():
        mlflow.log_param("checkpoint_path", str(checkpoint_path))
        mlflow.log_param("checkpoint_epoch", checkpoint.get("epoch"))
        for key, value in params.items():
            mlflow.log_param(key, value)
        mlflow.log_metric("reports", report["reports"])
        mlflow.log_metric("any_detector_flag_rate", report["any_detector_flag_rate"])
        mlflow.log_metric("two_or_more_detectors_flag_rate", report["two_or_more_detectors_flag_rate"])
        for name, value in report["error_percentiles"].items():
            mlflow.log_metric(f"error_{name.replace('.', '_')}", value)
        if report["flag_rate_at_operating_threshold"] is not None:
            mlflow.log_metric("flag_rate_at_operating_threshold", report["flag_rate_at_operating_threshold"])
        mlflow.log_dict(report, "report.json")


async def run(
    dsn: str,
    checkpoint_path: Path,
    eval_start: datetime,
    eval_end: datetime,
    device_name: str | None,
    skip_mlflow: bool,
    source: TrainingDataSource = "live",
    batch_size: int = DEFAULT_EVAL_BATCH_SIZE,
    include_training_vessels: bool = False,
    max_windows: int | None = None,
    out_path: Path | None = None,
    freeze_threshold: float = DEFAULT_FREEZE_REPLAY_THRESHOLD,
    speed_jump_threshold: float | None = None,
) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    device = torch.device(device_name) if device_name else torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, checkpoint = load_checkpoint(checkpoint_path, device)
    fallback = "unknown (checkpoint predates provenance fields)"
    print(f"scoring checkpoint={checkpoint_path} epoch={checkpoint.get('epoch')} train_loss={checkpoint.get('train_loss')} val_loss={checkpoint.get('val_loss')}")
    print(f"checkpoint training provenance: source={checkpoint.get('source', fallback)} start={checkpoint.get('start', fallback)} end={checkpoint.get('end', fallback)}")
    scope = "all vessels" if include_training_vessels else HOLDOUT_METHOD
    print(f"eval range: {eval_start.isoformat()}..{eval_end.isoformat()} (device={device}, source={source}, vessels: {scope})")

    parts: list[ReportScores] = []
    batch: list[FeatureWindow] = []
    seen_windows = 0

    def _score_batch() -> None:
        if not batch:
            return
        parts.append(score_windows(model, batch))
        if len(parts) == 1 or len(parts) % PROFILE_EVERY_BATCHES == 0:
            print(f"scored batch={len(parts):,} windows={seen_windows:,}", flush=True)
        batch.clear()

    async for window in stream_feature_windows(
        dsn, eval_start, eval_end, source=source, max_windows=max_windows, progress_every=50_000,
        on_progress=lambda rows, vessels, windows: print(f"loading: {rows:,} rows | {vessels:,} vessels | {windows:,} windows", flush=True),
    ):
        if not include_training_vessels and not is_validation_vessel(window.mmsi):
            continue
        batch.append(window)
        seen_windows += 1
        if len(batch) >= batch_size:
            _score_batch()
    _score_batch()

    if not parts:
        print(f"no windows found for {eval_start.isoformat()}..{eval_end.isoformat()} (source={source})", file=sys.stderr)
        return 1

    report = build_report(merge_scores(parts), OPERATING_THRESHOLD, freeze_threshold, speed_jump_threshold)
    report["checkpoint"] = {"path": str(checkpoint_path), "epoch": checkpoint.get("epoch")}
    report["eval"] = {"source": source, "start": eval_start.isoformat(), "end": eval_end.isoformat(), "vessels": scope}
    print_report(report)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {out_path}")
    if not skip_mlflow:
        _log_mlflow(report, checkpoint_path, checkpoint, {"eval_source": source, "eval_start": eval_start.isoformat(), "eval_end": eval_end.isoformat(), "vessels": scope})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--checkpoint", required=True, help="Path to a .pt checkpoint, e.g. checkpoints/epoch_010.pt")
    parser.add_argument("--source", choices=("live", "historical"), default="live")
    parser.add_argument("--eval-start", required=True, help="ISO date/datetime, start of the range")
    parser.add_argument("--eval-end", required=True, help="ISO date/datetime, end of the range")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_EVAL_BATCH_SIZE, help="Windows per model batch.")
    parser.add_argument("--device", choices=("cpu", "cuda"), help="Defaults to cuda if available, else cpu.")
    parser.add_argument("--include-training-vessels", action="store_true", help="Score every vessel, not only the validation split.")
    parser.add_argument("--max-windows", type=int, help="Development cap on windows read from the database.")
    parser.add_argument("--freeze-replay-threshold", type=float, default=DEFAULT_FREEZE_REPLAY_THRESHOLD)
    parser.add_argument("--speed-jump-threshold", type=float, default=None, help="Off unless given, like the live scorer.")
    parser.add_argument("--out", type=Path, help="Write the report as JSON here.")
    parser.add_argument("--no-mlflow", action="store_true", help="Skip MLflow logging.")
    args = parser.parse_args(argv)
    return asyncio.run(run(
        args.dsn, Path(args.checkpoint), datetime.fromisoformat(args.eval_start), datetime.fromisoformat(args.eval_end),
        args.device, args.no_mlflow, args.source, args.batch_size, args.include_training_vessels, args.max_windows,
        args.out, args.freeze_replay_threshold, args.speed_jump_threshold,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
