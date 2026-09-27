from datetime import datetime, timedelta, timezone

from tools.freeze_corroboration import corroborate_freeze_replay


def _row(minutes: int, latitude: float, longitude: float, sog_knots: float) -> dict:
    return {
        "received_at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        "latitude": latitude,
        "longitude": longitude,
        "sog_knots": sog_knots,
        "cog_deg": 90.0,
    }


def test_no_positions_key_does_not_crash() -> None:
    result = corroborate_freeze_replay({})
    assert result == {"matched": False, "frozen_reports": 0, "total_pairs": 0}


def test_single_position_has_no_pairs_to_compare() -> None:
    result = corroborate_freeze_replay({"positions": [_row(0, 10.0, 20.0, 8.0)]})
    assert result == {"matched": False, "frozen_reports": 0, "total_pairs": 0}


def test_genuine_motion_does_not_match() -> None:
    # Reported speed and position both move consistently - not a freeze.
    positions = [_row(0, 10.0, 20.0, 8.0), _row(1, 10.01, 20.01, 8.0)]
    result = corroborate_freeze_replay({"positions": positions})
    assert result["matched"] is False
    assert result["total_pairs"] == 1
    assert result["frozen_reports"] == 0


def test_frozen_position_with_claimed_speed_matches() -> None:
    # Same lat/lon across reports (a replayed/frozen position) while sog_knots
    # keeps claiming the vessel is under way.
    positions = [_row(0, 10.0, 20.0, 12.0), _row(1, 10.0, 20.0, 12.0), _row(2, 10.0, 20.0, 12.0)]
    result = corroborate_freeze_replay({"positions": positions})
    assert result["matched"] is True
    assert result["total_pairs"] == 2
    assert result["frozen_reports"] == 2


def test_frozen_position_with_near_zero_claimed_speed_does_not_match() -> None:
    # Position is frozen, but sog_knots also (honestly) reports near-zero speed -
    # a genuinely stationary vessel, not a freeze/replay spoof.
    positions = [_row(0, 10.0, 20.0, 0.1), _row(1, 10.0, 20.0, 0.1)]
    result = corroborate_freeze_replay({"positions": positions})
    assert result["matched"] is False
    assert result["total_pairs"] == 1
    assert result["frozen_reports"] == 0


def test_missing_sog_on_a_report_is_skipped_not_counted() -> None:
    positions = [_row(0, 10.0, 20.0, 12.0), {**_row(1, 10.0, 20.0, 12.0), "sog_knots": None}]
    result = corroborate_freeze_replay({"positions": positions})
    assert result == {"matched": False, "frozen_reports": 0, "total_pairs": 0}


def test_out_of_order_timestamps_are_skipped_not_counted() -> None:
    # implied_speed_knots returns the MISSING_VALUE sentinel for non-positive
    # elapsed time; this must not be mistaken for a near-zero (frozen) speed.
    positions = [_row(1, 10.0, 20.0, 12.0), _row(0, 10.0, 20.0, 12.0)]
    result = corroborate_freeze_replay({"positions": positions})
    assert result == {"matched": False, "frozen_reports": 0, "total_pairs": 0}
