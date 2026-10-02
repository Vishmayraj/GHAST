from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from features.quality import (
    MAX_SCOREABLE_GAP_SECONDS, REASON_ANTIMERIDIAN, REASON_INVALID_COORDINATE, REASON_LONG_GAP,
    REASON_NON_POSITIVE_DT, count_reasons, scoreable_mask, transition_reasons, valid_coordinate,
)

T0 = datetime(2026, 9, 30, tzinfo=timezone.utc)


def times(*seconds: float):
    return [T0 + timedelta(seconds=s) for s in seconds]


def test_clean_steps_are_scoreable():
    positions = np.array([[10.0, 20.0], [10.001, 20.001], [10.002, 20.002]])
    assert transition_reasons(positions, times(0, 30, 60)) == [None, None, None]


@pytest.mark.parametrize("latitude,longitude,ok", [
    (91.0, 181.0, False), (91.0, 20.0, False), (10.0, 181.0, False), (-90.5, 0.0, False),
    (float("nan"), 0.0, False), (0.0, float("inf"), False),
    (90.0, 180.0, True), (-90.0, -180.0, True), (0.0, 0.0, True),
])
def test_valid_coordinate(latitude, longitude, ok):
    assert valid_coordinate(latitude, longitude) is ok


def test_sentinel_coordinate_taints_the_step_in_and_the_step_out():
    positions = np.array([[10.0, 20.0], [91.0, 181.0], [10.0, 20.0]])
    assert transition_reasons(positions, times(0, 30, 60)) == [None, REASON_INVALID_COORDINATE, REASON_INVALID_COORDINATE]


def test_duplicate_and_out_of_order_timestamps_are_non_positive_dt():
    positions = np.array([[10.0, 20.0], [10.0, 20.0], [10.0, 20.0]])
    assert transition_reasons(positions, times(0, 0, -5)) == [None, REASON_NON_POSITIVE_DT, REASON_NON_POSITIVE_DT]


def test_long_gap_is_skipped_but_the_limit_itself_is_scored():
    positions = np.array([[10.0, 20.0], [10.1, 20.1], [10.2, 20.2]])
    gap = MAX_SCOREABLE_GAP_SECONDS
    assert transition_reasons(positions, times(0, gap, gap + gap + 1)) == [None, None, REASON_LONG_GAP]


def test_antimeridian_crossing_is_skipped_not_read_as_a_359_degree_jump():
    positions = np.array([[-17.0, 179.99], [-17.0, -179.99]])
    assert transition_reasons(positions, times(0, 60)) == [None, REASON_ANTIMERIDIAN]


def test_invalid_coordinate_wins_over_other_reasons():
    positions = np.array([[10.0, 20.0], [91.0, 181.0]])
    assert transition_reasons(positions, times(0, 0)) == [None, REASON_INVALID_COORDINATE]


def test_count_reasons_ignores_clean_reports():
    assert count_reasons([None, REASON_LONG_GAP, REASON_LONG_GAP, None, REASON_ANTIMERIDIAN]) == {REASON_LONG_GAP: 2, REASON_ANTIMERIDIAN: 1}


def test_length_mismatch_is_an_error():
    with pytest.raises(ValueError):
        transition_reasons(np.zeros((3, 2)), times(0, 1))


def test_scoreable_mask_aligns_with_reports_one_to_n_minus_one():
    positions = np.array([[10.0, 20.0], [10.001, 20.0], [91.0, 181.0], [10.003, 20.0], [10.004, 20.0]])
    keep, left_out = scoreable_mask(positions, times(0, 30, 60, 90, 120))
    assert keep.tolist() == [True, False, False, True]  # reports 1..4; the sentinel taints the step in and the step out
    assert left_out == [REASON_INVALID_COORDINATE, REASON_INVALID_COORDINATE]
    assert len(keep) == len(positions) - 1


def test_scoreable_mask_keeps_everything_on_clean_data():
    positions = np.cumsum(np.full((20, 2), 0.001), axis=0) + (10.0, 20.0)
    keep, left_out = scoreable_mask(positions, times(*range(0, 1200, 60)))
    assert keep.all() and keep.size == 19 and left_out == []
