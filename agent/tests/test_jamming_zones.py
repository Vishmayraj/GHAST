import pytest
from datetime import datetime, timezone

from tools.jamming_zones import JAMMING_ZONE_QUERY, check_jamming_zones


class _FakeConnection:
    def __init__(self, row=None) -> None:
        self.row = row
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self.row


@pytest.mark.asyncio
async def test_jamming_zone_query_casts_geography_to_geometry() -> None:
    connection = _FakeConnection({"name": "test zone", "confidence": 0.9})
    flagged_at = datetime(2026, 9, 29, tzinfo=timezone.utc)

    result = await check_jamming_zones(connection, 10.0, 20.0, flagged_at)

    query, args = connection.calls[0]
    assert query == JAMMING_ZONE_QUERY
    assert "zone::geometry" in query
    assert "ST_SetSRID(ST_MakePoint($2, $1), 4326)" in query
    assert "::geography" not in query
    assert args == (10.0, 20.0, flagged_at)
    assert result == {"matched": True, "zone": {"name": "test zone", "confidence": 0.9}}
