import pytest
from datetime import datetime, timezone

from tools.track_history import TRACK_AFTER_QUERY, TRACK_HISTORY_QUERY, get_track_history


class _FakeConnection:
    def __init__(self) -> None:
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return [{"received_at": args[1], "latitude": 1.0, "longitude": 2.0}]


@pytest.mark.asyncio
async def test_track_history_uses_timestamp_typed_interval_expression() -> None:
    connection = _FakeConnection()
    flagged_at = datetime(2026, 9, 29, tzinfo=timezone.utc)

    result = await get_track_history(connection, 123, flagged_at, hours=6)

    query, args = connection.calls[0]
    assert query == TRACK_HISTORY_QUERY
    assert "$2::timestamptz" in query
    assert "INTERVAL '1 hour'" in query
    assert args == (123, flagged_at, 6)
    assert result["positions"][0]["latitude"] == 1.0


@pytest.mark.asyncio
async def test_track_history_stops_at_the_flag_and_has_no_after_slice_by_default() -> None:
    connection = _FakeConnection()
    flagged_at = datetime(2026, 9, 29, tzinfo=timezone.utc)

    result = await get_track_history(connection, 123, flagged_at)

    # one query only, and its upper bound is the flag itself, never flag plus hours
    assert len(connection.calls) == 1
    assert "AND $2::timestamptz\n" in TRACK_HISTORY_QUERY
    assert "$2::timestamptz +" not in TRACK_HISTORY_QUERY
    assert "after" not in result


@pytest.mark.asyncio
async def test_track_history_after_slice_is_separate_and_opt_in() -> None:
    connection = _FakeConnection()
    flagged_at = datetime(2026, 9, 29, tzinfo=timezone.utc)

    result = await get_track_history(connection, 123, flagged_at, hours=6, after_hours=2)

    assert [query for query, _ in connection.calls] == [TRACK_HISTORY_QUERY, TRACK_AFTER_QUERY]
    assert connection.calls[1][1] == (123, flagged_at, 2)
    assert "received_at > $2::timestamptz" in TRACK_AFTER_QUERY
    assert len(result["positions"]) == 1 and len(result["after"]) == 1
