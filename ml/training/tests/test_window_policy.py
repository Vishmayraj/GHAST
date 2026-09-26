from datetime import datetime, timedelta, timezone

from training.window_policy import initial_live_window, rolling_live_window

FIRST_LIVE = datetime(2026, 9, 26, 17, 15, 14, tzinfo=timezone.utc)


def test_initial_window_is_anchored_to_first_live_report() -> None:
    decision = initial_live_window(FIRST_LIVE, FIRST_LIVE + timedelta(days=14), FIRST_LIVE + timedelta(days=15))
    assert decision.ready and decision.start == FIRST_LIVE and decision.end == FIRST_LIVE + timedelta(days=14)


def test_initial_window_waits_for_coverage_and_operational_delay() -> None:
    assert not initial_live_window(FIRST_LIVE, FIRST_LIVE + timedelta(days=13), FIRST_LIVE + timedelta(days=20)).ready
    assert not initial_live_window(FIRST_LIVE, FIRST_LIVE + timedelta(days=14), FIRST_LIVE + timedelta(days=14)).ready


def test_rolling_window_tracks_latest_live_report() -> None:
    latest = FIRST_LIVE + timedelta(days=30)
    decision = rolling_live_window(FIRST_LIVE, latest)
    assert decision.ready and decision.start == latest - timedelta(days=14) and decision.end == latest
