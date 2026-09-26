"""Streaming-cursor behaviour: vessel-boundary flushing and dev sampling limits.

Uses a fake asyncpg connection (plain dicts as rows) rather than a real
database, since the streaming contract under test is about *when* windows
get flushed and *when* the read stops, not about SQL itself.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from features.pipeline import MINIMUM_REPORTS_PER_VESSEL, _stream_rows_from_connection

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _row(index: int, mmsi: int) -> dict[str, object]:
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


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for row in self._rows:
            yield row


class FakeConnection:
    """Enough of asyncpg.Connection's surface for _stream_rows_from_connection."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    def cursor(self, query: str, *args: object, prefetch: int | None = None) -> _FakeCursor:
        return _FakeCursor(self._rows)


async def _collect(connection: FakeConnection, **kwargs) -> list:
    return [window async for window in _stream_rows_from_connection(connection, START, END, "live", **kwargs)]


def test_windows_flush_at_each_vessel_boundary_not_at_the_end() -> None:
    rows = [_row(i, mmsi=1) for i in range(MINIMUM_REPORTS_PER_VESSEL)] + [_row(i, mmsi=2) for i in range(MINIMUM_REPORTS_PER_VESSEL)]
    windows = asyncio.run(_collect(FakeConnection(rows)))
    assert [window.mmsi for window in windows] == [1, 2]


def test_short_final_vessel_run_is_still_flushed() -> None:
    """The last vessel in the stream has no following vessel to trigger a flush; it must
    still be emitted once the cursor is exhausted, not silently dropped."""
    rows = [_row(i, mmsi=1) for i in range(MINIMUM_REPORTS_PER_VESSEL * 2)] + [_row(i, mmsi=2) for i in range(MINIMUM_REPORTS_PER_VESSEL)]
    windows = asyncio.run(_collect(FakeConnection(rows)))
    assert [window.mmsi for window in windows] == [1, 1, 2]


def test_vessel_with_too_few_reports_yields_no_window() -> None:
    rows = [_row(i, mmsi=1) for i in range(MINIMUM_REPORTS_PER_VESSEL - 1)] + [_row(i, mmsi=2) for i in range(MINIMUM_REPORTS_PER_VESSEL)]
    windows = asyncio.run(_collect(FakeConnection(rows)))
    assert [window.mmsi for window in windows] == [2]


def test_max_vessels_stops_after_the_requested_count() -> None:
    rows = []
    for mmsi in (1, 2, 3):
        rows += [_row(i, mmsi=mmsi) for i in range(MINIMUM_REPORTS_PER_VESSEL)]
    windows = asyncio.run(_collect(FakeConnection(rows), max_vessels=2))
    assert {window.mmsi for window in windows} == {1, 2}


def test_max_windows_stops_mid_stream() -> None:
    rows = []
    for mmsi in (1, 2, 3):
        rows += [_row(i, mmsi=mmsi) for i in range(MINIMUM_REPORTS_PER_VESSEL)]
    windows = asyncio.run(_collect(FakeConnection(rows), max_windows=2))
    assert len(windows) == 2


def test_max_rows_can_cut_off_the_final_vessel_before_its_floor() -> None:
    rows = [_row(i, mmsi=1) for i in range(MINIMUM_REPORTS_PER_VESSEL)] + [_row(i, mmsi=2) for i in range(MINIMUM_REPORTS_PER_VESSEL)]
    windows = asyncio.run(_collect(FakeConnection(rows), max_rows=MINIMUM_REPORTS_PER_VESSEL + 5))
    assert [window.mmsi for window in windows] == [1]


def test_progress_callback_reports_final_totals() -> None:
    rows = [_row(i, mmsi=1) for i in range(MINIMUM_REPORTS_PER_VESSEL)] + [_row(i, mmsi=2) for i in range(MINIMUM_REPORTS_PER_VESSEL)]
    calls: list[tuple[int, int, int]] = []
    asyncio.run(_collect(FakeConnection(rows), on_progress=lambda *args: calls.append(args), progress_every=1))
    assert calls[-1] == (2 * MINIMUM_REPORTS_PER_VESSEL, 2, 2)
