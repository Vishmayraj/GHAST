from datetime import datetime, timedelta, timezone

from tools.freeze_corroboration import FREEZE_WINDOW_REPORTS, MIN_FROZEN_PAIRS, corroborate_freeze_replay


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
    positions = [_row(minute, 10.0, 20.0, 12.0) for minute in range(MIN_FROZEN_PAIRS + 1)]
    result = corroborate_freeze_replay({"positions": positions})
    assert result["matched"] is True
    assert result["total_pairs"] == MIN_FROZEN_PAIRS
    assert result["frozen_reports"] == MIN_FROZEN_PAIRS


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


def test_fewer_than_the_minimum_frozen_pairs_does_not_match() -> None:
    # One bad SOG at rest gives a single frozen pair; that must not corroborate anything.
    positions = [_row(minute, 10.0, 20.0, 12.0) for minute in range(MIN_FROZEN_PAIRS)]
    result = corroborate_freeze_replay({"positions": positions})
    assert result["frozen_reports"] == MIN_FROZEN_PAIRS - 1
    assert result["matched"] is False


def test_single_frozen_pair_among_moving_reports_does_not_match() -> None:
    positions = [_row(0, 10.0, 20.0, 12.0), _row(1, 10.0, 20.0, 12.0)]
    positions += [_row(minute, 10.0 + 0.01 * minute, 20.0, 12.0) for minute in range(2, 12)]
    result = corroborate_freeze_replay({"positions": positions})
    assert result["frozen_reports"] == 1
    assert result["matched"] is False


def test_only_the_last_window_of_reports_is_examined() -> None:
    # A frozen stretch far back in a long track is outside the scored window and must not count.
    old_frozen = [_row(minute, 10.0, 20.0, 12.0) for minute in range(MIN_FROZEN_PAIRS + 1)]
    recent_moving = [_row(100 + minute, 11.0 + 0.01 * minute, 21.0, 12.0) for minute in range(FREEZE_WINDOW_REPORTS)]
    result = corroborate_freeze_replay({"positions": old_frozen + recent_moving})
    assert result["frozen_reports"] == 0
    assert result["total_pairs"] == FREEZE_WINDOW_REPORTS - 1
    assert result["matched"] is False
