"""summary.py must be deterministic and must make each pattern visible in the text.

The altered tracks below are built by hand from a clean track. They are unit-test fixtures for the
summary function, not a data source.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from features.extract import COG_INDEX, N_FEATURES, SOG_INDEX
from features.pipeline import FeatureWindow
from features.summary import (
    PATTERN_LABELS, pattern_questions, summarize_rows, window_to_rows,
)

START = datetime(2026, 9, 1, tzinfo=timezone.utc)


def clean_window(n: int = 20) -> FeatureWindow:
    """~10 knots due north, one report a minute (0.0028 deg lat is about 0.31 km)."""
    features = np.zeros((n, N_FEATURES), dtype=np.float32)
    features[:, SOG_INDEX] = 10.0
    features[:, COG_INDEX] = 0.0
    positions = np.array([(51.0 + i * 0.0028, 4.0) for i in range(n)], dtype=np.float64)
    timestamps = tuple(START + timedelta(minutes=i) for i in range(n))
    return FeatureWindow(1, timestamps[0], timestamps[-1], features, positions, timestamps)


def with_teleport(window: FeatureWindow, index: int = 10, km: float = 55.0) -> FeatureWindow:
    """One report moved far from the track."""
    positions = window.positions.copy()
    positions[index] += (km / 111.0, 0.0)
    return FeatureWindow(window.mmsi, window.window_start, window.window_end, window.features.copy(), positions, window.timestamps)


def with_replayed_stretch(window: FeatureWindow, start: int = 10, length: int = 6) -> FeatureWindow:
    """Earlier positions copied forward over later reports, so the track loops back."""
    positions = window.positions.copy()
    positions[start:start + length] = positions[start - length:start]
    return FeatureWindow(window.mmsi, window.window_start, window.window_end, window.features.copy(), positions, window.timestamps)


def with_flipped_course(window: FeatureWindow, index: int = 10) -> FeatureWindow:
    """Reported course rotated away from the direction of travel at one report."""
    features = window.features.copy()
    features[index, COG_INDEX] = (features[index, COG_INDEX] + 150.0) % 360.0
    return FeatureWindow(window.mmsi, window.window_start, window.window_end, features, window.positions.copy(), window.timestamps)


def fields(text: str) -> dict[str, str]:
    return dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)


def test_summary_is_deterministic() -> None:
    rows = window_to_rows(clean_window())
    assert summarize_rows(rows) == summarize_rows(list(reversed(rows)))  # order-insensitive too


def test_clean_window_looks_normal() -> None:
    text = summarize_rows(window_to_rows(clean_window()))
    assert "steps_claiming_speed_but_not_moving: 0, longest_run: 0" in text
    assert "max_course_vs_travel_direction_deg: 0" in text
    assert "max_implied_over_max_reported: 1.0" in text


def test_teleport_shows_huge_implied_speed() -> None:
    injected = with_teleport(clean_window())
    text = summarize_rows(window_to_rows(injected))
    assert float(fields(text)["max_step_km"]) > 5.0
    assert float(fields(text)["max_implied_over_max_reported"]) > 20.0


def test_freeze_replay_shows_stationary_claims() -> None:
    injected = with_replayed_stretch(clean_window())
    fields_ = fields(summarize_rows(window_to_rows(injected)))
    # A replayed stretch loops back over ground already covered, rather than holding one position still.
    assert int(fields_["reports_repeating_earlier_positions"]) > 0


def test_true_freeze_shows_stationary_claims() -> None:
    window = clean_window()
    window.positions[10:] = window.positions[10]  # vessel "stops" but keeps claiming 10 knots
    text = summarize_rows(window_to_rows(window))
    assert "steps_claiming_speed_but_not_moving: 0," not in text


def test_clean_window_repeats_no_positions() -> None:
    assert "reports_repeating_earlier_positions: 0" in summarize_rows(window_to_rows(clean_window()))


def test_impossible_kinematics_shows_course_mismatch() -> None:
    injected = with_flipped_course(clean_window())
    text = summarize_rows(window_to_rows(injected))
    assert float(fields(text)["max_course_vs_travel_direction_deg"]) >= 90.0
    assert "steps_claiming_speed_but_not_moving: 0, longest_run: 0" in text


def test_stale_cached_implied_speed_column_is_ignored() -> None:
    # track_history rows have no cached implied-speed column, so the summary must not use one.
    window = clean_window()
    injected = with_teleport(window)
    assert np.array_equal(injected.features, window.features)
    assert summarize_rows(window_to_rows(injected)) != summarize_rows(window_to_rows(window))


def test_too_short_track_does_not_raise() -> None:
    assert "not enough reports" in summarize_rows(window_to_rows(clean_window(1)))


def test_labels_and_question_agree() -> None:
    criteria = pattern_questions()["pattern"]["criteria"]
    assert tuple(criteria) == PATTERN_LABELS
    assert len(set(PATTERN_LABELS)) == len(PATTERN_LABELS)
