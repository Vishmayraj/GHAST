"""The evaluation harness: score any detector against a labeled dataset.

Usage as a library:
    from evaluation.datasets import load_gps_spoofing_mass
    from evaluation.baselines import prediction_error_detector
    from evaluation.harness import evaluate

    observations = load_gps_spoofing_mass("path/to/gps_spoofing data.csv")
    result = evaluate(observations, prediction_error_detector, threshold=0.001)
    print(result.metrics.precision, result.metrics.recall, result.metrics.f1)

Usage from the command line:
    cd ml
    python -m evaluation.harness --dataset gps_spoofing_mass \\
        --path ../data/research_datasets/gps_spoofing_mass/gps_spoofing_data.csv \\
        --detector prediction_error --threshold 0.001
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from .baselines import Detector, prediction_error_detector, speed_jump_detector
from .datasets import DATASET_LOADERS, AISObservation
from .metrics import EvaluationMetrics, precision_recall_f1

DETECTORS: dict[str, Detector] = {
    "prediction_error": prediction_error_detector,
    "speed_jump": speed_jump_detector,
}


@dataclass(frozen=True)
class EvaluationResult:
    metrics: EvaluationMetrics
    threshold: float
    n_observations: int


def evaluate(
    observations: list[AISObservation], detector: Detector, threshold: float
) -> EvaluationResult:
    """Score every observation with `detector`, predict "spoofed" wherever
    the score exceeds `threshold`, and compute precision/recall/F1 against
    the dataset's own labels."""
    y_true = [obs.is_spoofed for obs in observations]
    y_pred = [detector(obs) > threshold for obs in observations]
    metrics = precision_recall_f1(y_true, y_pred)
    return EvaluationResult(metrics=metrics, threshold=threshold, n_observations=len(observations))


def _print_result(result: EvaluationResult) -> None:
    m = result.metrics
    print(f"n = {result.n_observations}, threshold = {result.threshold}")
    print(f"  precision = {m.precision:.3f}")
    print(f"  recall    = {m.recall:.3f}")
    print(f"  f1        = {m.f1:.3f}")
    print(f"  accuracy  = {m.accuracy:.3f}")
    c = m.confusion
    print(f"  confusion: tp={c.true_positive} fp={c.false_positive} tn={c.true_negative} fn={c.false_negative}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, choices=sorted(DATASET_LOADERS))
    parser.add_argument("--path", required=True, help="Path to the dataset file")
    parser.add_argument("--detector", required=True, choices=sorted(DETECTORS))
    parser.add_argument("--threshold", type=float, required=True)
    args = parser.parse_args(argv)

    load = DATASET_LOADERS[args.dataset]
    observations = load(args.path)
    if not observations:
        print(f"No labeled observations loaded from {args.path}", file=sys.stderr)
        return 1

    detector = DETECTORS[args.detector]
    result = evaluate(observations, detector, args.threshold)
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
