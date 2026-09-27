"""CPU-fast tests: a tiny untrained in-memory model, synthetic windows, no real
database or checkpoint file. Mirrors the CPU-only smoke-test discipline
ml/models/bilstm/tests/test_model.py already uses - these prove the scoring/sweep/
breakdown wiring works, not that any particular trained checkpoint is good.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from evaluation.baselines import freeze_replay_detector, prediction_error_detector, speed_jump_detector
from evaluation.score_checkpoint import (
    per_pattern_breakdown,
    score_injected_windows,
    sweep_thresholds,
    threshold_candidates,
)
from features.extract import N_FEATURES
from features.inject import SPOOF_PATTERNS, build_synthetic_dataset
from features.pipeline import FeatureWindow
from models.bilstm.model import BiLSTMNextDelta
from models.bilstm.infer import prediction_errors, prediction_errors_batch

WINDOW_LENGTH = 20


def _clean_window(mmsi: int, seed: int) -> FeatureWindow:
    rng = np.random.default_rng(seed)
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    positions = np.cumsum(
        np.column_stack([rng.normal(0, 0.001, WINDOW_LENGTH), rng.normal(0, 0.001, WINDOW_LENGTH)]), axis=0
    ) + (10.0, 70.0)
    features = np.tile(np.array([10.0, 90.0, 90.0, 0.0, 70.0, 10.0, 0.0, 0.0], dtype=np.float32), (WINDOW_LENGTH, 1))
    timestamps = tuple(start + timedelta(minutes=index) for index in range(WINDOW_LENGTH))
    return FeatureWindow(mmsi, timestamps[0], timestamps[-1], features, positions, timestamps)


@pytest.fixture
def tiny_model() -> BiLSTMNextDelta:
    model = BiLSTMNextDelta(N_FEATURES)  # untrained, random weights - CPU-fast, no checkpoint needed
    model.eval()
    return model


@pytest.fixture
def injected_windows():
    # Fixed seeds throughout: with seed=0 this fixture deterministically produces
    # 4 control windows and 8 spoofed windows across multiple patterns, which is
    # what the assertions below rely on.
    windows = [_clean_window(mmsi, seed=mmsi) for mmsi in range(1, 13)]
    return build_synthetic_dataset(windows, seed=0)


def test_score_injected_windows_populates_prediction_error(tiny_model, injected_windows):
    observations = score_injected_windows(tiny_model, injected_windows)
    assert len(observations) == sum(len(w.is_spoofed) for w in injected_windows)
    assert all(o.prediction_error is not None for o in observations)


def test_batched_prediction_errors_match_single_window_scoring(tiny_model, injected_windows):
    windows = [injected.window for injected in injected_windows]
    batched = prediction_errors_batch(tiny_model, windows)
    single = [prediction_errors(tiny_model, window) for window in windows]
    assert len(batched) == len(single)
    for batch_errors, single_errors in zip(batched, single, strict=True):
        np.testing.assert_allclose(batch_errors, single_errors)


def test_score_injected_windows_first_report_per_window_has_zero_error(tiny_model, injected_windows):
    # infer.py::prediction_errors leaves the first timestep at 0.0 - there is no
    # preceding prediction to compare it against.
    observations = score_injected_windows(tiny_model, injected_windows)
    first_per_window = observations[0::WINDOW_LENGTH]
    assert all(o.prediction_error == pytest.approx(0.0) for o in first_per_window)


def test_score_injected_windows_carries_pattern_through(tiny_model, injected_windows):
    observations = score_injected_windows(tiny_model, injected_windows)
    observed_patterns = {o.pattern for o in observations}
    assert None in observed_patterns
    assert observed_patterns - {None} <= set(SPOOF_PATTERNS)
    assert observed_patterns - {None}  # at least one real spoof pattern present


def test_threshold_candidates_handles_all_zero_scores():
    # A degenerate all-zero score distribution (e.g. speed_jump with no usable
    # acceleration signal) must not crash the percentile sweep.
    assert threshold_candidates([0.0, 0.0, 0.0]) == [0.0]


def test_threshold_candidates_returns_sorted_unique_values(tiny_model, injected_windows):
    observations = score_injected_windows(tiny_model, injected_windows)
    scores = [prediction_error_detector(o) for o in observations]
    candidates = threshold_candidates(scores)
    assert candidates == sorted(set(candidates))
    assert len(candidates) > 0


def test_threshold_sweep_runs_without_error(tiny_model, injected_windows):
    observations = score_injected_windows(tiny_model, injected_windows)
    scores = [prediction_error_detector(o) for o in observations]
    thresholds = threshold_candidates(scores)
    sweep = sweep_thresholds(observations, "prediction_error", prediction_error_detector, thresholds)
    assert len(sweep.results) == len(thresholds)
    assert all(0.0 <= result.metrics.f1 <= 1.0 for result in sweep.results)


def test_threshold_sweep_best_by_f1_picks_the_maximum(tiny_model, injected_windows):
    observations = score_injected_windows(tiny_model, injected_windows)
    scores = [prediction_error_detector(o) for o in observations]
    sweep = sweep_thresholds(observations, "prediction_error", prediction_error_detector, threshold_candidates(scores))
    best = sweep.best_by_f1()
    assert all(best.metrics.f1 >= result.metrics.f1 for result in sweep.results)


def test_per_pattern_breakdown_separates_control_from_spoofed(tiny_model, injected_windows):
    observations = score_injected_windows(tiny_model, injected_windows)
    breakdown = per_pattern_breakdown(observations, prediction_error_detector, threshold=0.0)
    assert "control" in breakdown
    assert set(breakdown) - {"control"} <= set(SPOOF_PATTERNS)
    # every observation is accounted for exactly once across the groups
    assert sum(result.n_observations for result in breakdown.values()) == len(observations)


def test_speed_jump_detector_also_scores_these_observations(tiny_model, injected_windows):
    # acceleration is score_injected_windows' own derived field, not something the
    # injectors label - this just checks the existing baseline can run on it too.
    observations = score_injected_windows(tiny_model, injected_windows)
    scores = [speed_jump_detector(o) for o in observations]
    assert len(scores) == len(observations)
    assert all(isinstance(score, float) for score in scores)


def test_freeze_replay_detector_also_scores_these_observations(tiny_model, injected_windows):
    # Per ImplementationPlans/Sem5_BigPass_LiveScoring_And_Laya.md section 3:
    # freeze_replay_detector reads observation.sog and observation.implied_speed,
    # both of which score_injected_windows populates (see that function's own
    # docstring) - confirms the sweep added in score_checkpoint.py::run() can
    # actually call this detector on injected-synthetic output without a missing-field
    # crash, the same guarantee the existing speed_jump test above pins for that detector.
    observations = score_injected_windows(tiny_model, injected_windows)
    scores = [freeze_replay_detector(o) for o in observations]
    assert len(scores) == len(observations)
    assert all(isinstance(score, float) for score in scores)
    assert all(score >= 0.0 for score in scores)
