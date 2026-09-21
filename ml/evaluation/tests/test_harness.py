from pathlib import Path

from evaluation.baselines import prediction_error_detector, speed_jump_detector
from evaluation.datasets import load_gps_spoofing_mass
from evaluation.harness import evaluate

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gps_spoofing_sample.csv"


def test_evaluate_returns_metrics_for_every_observation():
    observations = load_gps_spoofing_mass(FIXTURE_PATH)
    result = evaluate(observations, prediction_error_detector, threshold=0.0)
    assert result.n_observations == len(observations)
    assert result.metrics.confusion.total == len(observations)


def test_evaluate_threshold_zero_with_prediction_error_flags_something():
    # prediction_error is >= 0 for every row in this dataset; a threshold
    # of exactly 0 should predict "spoofed" for any row with a nonzero
    # prediction error, so this should not come back as an all-negative
    # (precision/recall both 0) run - that would mean the detector or the
    # harness's threshold comparison is broken, not that the signal is weak.
    observations = load_gps_spoofing_mass(FIXTURE_PATH)
    result = evaluate(observations, prediction_error_detector, threshold=0.0)
    assert result.metrics.confusion.true_positive + result.metrics.confusion.false_positive > 0


def test_evaluate_with_unrealistically_high_threshold_predicts_nothing_positive():
    observations = load_gps_spoofing_mass(FIXTURE_PATH)
    result = evaluate(observations, prediction_error_detector, threshold=1_000_000.0)
    c = result.metrics.confusion
    assert c.true_positive == 0
    assert c.false_positive == 0


def test_evaluate_works_with_speed_jump_detector_too():
    observations = load_gps_spoofing_mass(FIXTURE_PATH)
    result = evaluate(observations, speed_jump_detector, threshold=0.0)
    assert result.n_observations == len(observations)
