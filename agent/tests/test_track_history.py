from datetime import datetime, timezone

from tools.track_history import TRACK_HISTORY_QUERY, get_track_history


class _FakeConnection:
    def __init__(self) -> None:
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return [{"received_at": args[1], "latitude": 1.0, "longitude": 2.0}]


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
