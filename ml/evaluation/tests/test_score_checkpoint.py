"""CPU-fast tests: a tiny untrained in-memory model and hand-built windows, no database and no
checkpoint file. They prove the real-data scoring wiring (flattening, vectorised detectors,
flag rates, calibration by alert budget), not that any trained checkpoint is good.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from evaluation.baselines import freeze_replay_detector
from evaluation.datasets import AISObservation
from evaluation.score_checkpoint import (
    build_report,
    flag_rate,
    freeze_replay_scores,
    merge_scores,
    score_windows,
    speed_jump_scores,
    threshold_for_flag_rate,
)
from features.extract import IMPLIED_SPEED_INDEX, MISSING_VALUE, N_FEATURES, SOG_INDEX
from features.pipeline import FeatureWindow
from models.bilstm.infer import prediction_errors, prediction_errors_batch
from models.bilstm.model import BiLSTMNextDelta

WINDOW_LENGTH = 20


def _window(mmsi: int, seed: int, implied_speed: float = 10.0) -> FeatureWindow:
    rng = np.random.default_rng(seed)
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    positions = np.cumsum(
        np.column_stack([rng.normal(0, 0.001, WINDOW_LENGTH), rng.normal(0, 0.001, WINDOW_LENGTH)]), axis=0
    ) + (10.0, 70.0)
    features = np.tile(np.array([10.0, 90.0, 90.0, 0.0, 70.0, implied_speed, 0.0, 0.0], dtype=np.float32), (WINDOW_LENGTH, 1))
    timestamps = tuple(start + timedelta(minutes=index) for index in range(WINDOW_LENGTH))
    return FeatureWindow(mmsi, timestamps[0], timestamps[-1], features, positions, timestamps)


@pytest.fixture
def tiny_model() -> BiLSTMNextDelta:
    model = BiLSTMNextDelta(N_FEATURES)  # untrained, random weights
    model.eval()
    return model


@pytest.fixture
def windows() -> list[FeatureWindow]:
    return [_window(1000 + i, seed=i, implied_speed=10.0 if i % 2 == 0 else 0.0) for i in range(6)]


def test_score_windows_flattens_reports_one_to_nineteen(tiny_model, windows):
    scores = score_windows(tiny_model, windows)
    assert scores.n_windows == len(windows)
    assert scores.n_reports == len(windows) * (WINDOW_LENGTH - 1)
    assert np.all(scores.errors >= 0)
    for array in (scores.sog, scores.implied_speed, scores.speed_change, scores.underway):
        assert array.shape == scores.errors.shape


def test_batched_prediction_errors_match_single_window_scoring(tiny_model, windows):
    batched = prediction_errors_batch(tiny_model, windows)
    for window, errors in zip(windows, batched, strict=True):
        np.testing.assert_allclose(errors, prediction_errors(tiny_model, window), rtol=1e-4, atol=1e-6)


def test_first_report_error_is_zero_and_dropped_from_scores(tiny_model, windows):
    for errors in prediction_errors_batch(tiny_model, windows):
        assert errors[0] == 0.0
    # 19 scored reports per window, so report 0 is never in the flat arrays.
    assert score_windows(tiny_model, windows[:1]).n_reports == WINDOW_LENGTH - 1


def test_missing_values_become_nan_not_real_numbers(tiny_model):
    window = _window(1, 0)
    window.features[3, SOG_INDEX] = MISSING_VALUE
    window.features[5, IMPLIED_SPEED_INDEX] = MISSING_VALUE
    scores = score_windows(tiny_model, [window])
    assert np.isnan(scores.sog[2])            # report 3 -> flat index 2
    assert np.isnan(scores.implied_speed[4])  # report 5 -> flat index 4
    assert np.isnan(scores.speed_change[2]) and np.isnan(scores.speed_change[3])


def test_underway_flag_is_window_level(tiny_model, windows):
    scores = score_windows(tiny_model, windows)
    per_window = scores.underway.reshape(len(windows), WINDOW_LENGTH - 1)
    assert [bool(row[0]) for row in per_window] == [i % 2 == 0 for i in range(len(windows))]
    assert all(row.all() or not row.any() for row in per_window)


def test_merge_scores_concatenates(tiny_model, windows):
    merged = merge_scores([score_windows(tiny_model, windows[:2]), score_windows(tiny_model, windows[2:])])
    assert merged.n_windows == len(windows)
    np.testing.assert_allclose(merged.errors, score_windows(tiny_model, windows).errors, rtol=1e-4, atol=1e-6)


@pytest.mark.parametrize("sog", [None, -1.0, 0.0, 0.4, 3.0, 12.0])
@pytest.mark.parametrize("implied", [None, -1.0, 0.0, 0.3, 0.5, 0.6, 9.0])
def test_vectorised_freeze_score_matches_the_baseline_detector(sog, implied):
    observation = AISObservation("1", "t", 0.0, 0.0, sog, None, None, False, implied_speed=implied)
    sog_array = np.array([np.nan if sog in (None, MISSING_VALUE) else sog])
    implied_array = np.array([np.nan if implied in (None, MISSING_VALUE) else implied])
    assert freeze_replay_scores(sog_array, implied_array)[0] == pytest.approx(freeze_replay_detector(observation))


def test_speed_jump_scores_treat_unknown_as_no_signal():
    np.testing.assert_array_equal(speed_jump_scores(np.array([np.nan, 2.5, 0.0])), [0.0, 2.5, 0.0])


def test_threshold_for_flag_rate_hits_the_requested_rate():
    errors = np.random.default_rng(0).exponential(size=100_000)
    for target in (0.05, 0.01, 0.001):
        threshold = threshold_for_flag_rate(errors, target)
        assert flag_rate(errors, threshold) == pytest.approx(target, abs=target * 0.1)


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 2.0])
def test_threshold_for_flag_rate_rejects_bad_targets(bad):
    with pytest.raises(ValueError):
        threshold_for_flag_rate(np.array([1.0, 2.0]), bad)


def test_build_report_is_consistent(tiny_model, windows):
    scores = score_windows(tiny_model, windows)
    threshold = float(np.median(scores.errors))
    report = build_report(scores, threshold)
    assert report["reports"] == scores.n_reports
    assert report["flag_rate_at_operating_threshold"] == pytest.approx(flag_rate(scores.errors, threshold))
    assert report["two_or_more_detectors_flag_rate"] <= report["any_detector_flag_rate"] <= 1.0
    assert report["by_motion"]["underway"]["reports"] + report["by_motion"]["stationary"]["reports"] == scores.n_reports
    assert set(report["threshold_for_flag_rate"]) == {"0.05", "0.01", "0.005", "0.001"}
    percentiles = list(report["error_percentiles"].values())
    assert percentiles == sorted(percentiles)


def test_build_report_without_an_operating_threshold_still_reports(tiny_model, windows):
    report = build_report(score_windows(tiny_model, windows), None)
    assert report["flag_rate_at_operating_threshold"] is None
    assert "prediction_error" not in report["detector_vote_rates"]
    assert "freeze_replay" in report["detector_vote_rates"]


def test_build_report_rejects_empty_input():
    empty = np.array([])
    from evaluation.score_checkpoint import ReportScores
    with pytest.raises(ValueError):
        build_report(ReportScores(empty, empty, empty, empty, empty.astype(bool), 0), 0.1)
