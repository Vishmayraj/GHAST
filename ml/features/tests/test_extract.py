from datetime import datetime, timedelta, timezone

import numpy as np

from features.extract import HEADING_MISSING_INDEX, IMPLIED_SPEED_INDEX, MISSING_VALUE, RATE_OF_TURN_MISSING_INDEX, extract_features
from features.pipeline import MINIMUM_REPORTS_PER_VESSEL, window_rows


def _row(index: int, mmsi: int = 123) -> dict[str, object]:
    return {
        "received_at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index),
        "mmsi": mmsi,
        "latitude": 10.0,
        "longitude": 20.0 + index * 0.01,
        "sog_knots": 12.0,
        "cog_deg": 90.0,
        "true_heading_deg": 90,
        "rate_of_turn": 0,
        "navigational_status": 0,
        "ship_type": 70,
    }


def test_implied_speed_uses_actual_irregular_elapsed_time() -> None:
    rows = [_row(0), _row(1)]
    rows[1]["received_at"] = rows[0]["received_at"] + timedelta(minutes=2)
    features = extract_features(rows, ship_type=70)
    assert features[1, IMPLIED_SPEED_INDEX] < 20
    rows[1]["received_at"] = rows[0]["received_at"] + timedelta(minutes=1)
    assert extract_features(rows, ship_type=70)[1, IMPLIED_SPEED_INDEX] == features[1, IMPLIED_SPEED_INDEX] * 2


def test_missing_optional_values_have_sentinel_and_mask() -> None:
    row = _row(0)
    row["true_heading_deg"] = None
    row["rate_of_turn"] = None
    features = extract_features([row], ship_type=70)
    assert features[0, 2] == MISSING_VALUE
    assert features[0, HEADING_MISSING_INDEX] == 1
    assert features[0, 3] == MISSING_VALUE
    assert features[0, RATE_OF_TURN_MISSING_INDEX] == 1


def test_windowing_applies_data_floor_and_non_overlapping_stride() -> None:
    assert window_rows([_row(index) for index in range(MINIMUM_REPORTS_PER_VESSEL - 1)]) == []
    assert len(window_rows([_row(index) for index in range(MINIMUM_REPORTS_PER_VESSEL)])) == 1
    windows = window_rows([_row(index) for index in range(MINIMUM_REPORTS_PER_VESSEL * 2)])
    assert len(windows) == 2
    assert np.array_equal(windows[0].positions[-1], np.array([10.0, 20.19]))
    assert np.array_equal(windows[1].positions[0], np.array([10.0, 20.2]))
    assert windows[0].timestamps[-1] < windows[1].timestamps[0]
