"""Trajectory evidence query.

`positions` only ever holds reports up to and including `flagged_at`, so evidence built
from it (freeze corroboration, the Laya window, the report's last position) cannot
describe anything after the flag. Reports after the flag are available separately under
`after`, and only when the caller asks for them with `after_hours`.
"""
from __future__ import annotations


TRACK_HISTORY_QUERY = """
SELECT received_at, latitude, longitude, sog_knots, cog_deg
FROM vessel_position
WHERE mmsi = $1
  AND received_at BETWEEN
      $2::timestamptz - ($3::double precision * INTERVAL '1 hour')
      AND $2::timestamptz
ORDER BY received_at
"""

TRACK_AFTER_QUERY = """
SELECT received_at, latitude, longitude, sog_knots, cog_deg
FROM vessel_position
WHERE mmsi = $1
  AND received_at > $2::timestamptz
  AND received_at <= $2::timestamptz + ($3::double precision * INTERVAL '1 hour')
ORDER BY received_at
"""


async def get_track_history(connection, mmsi: int, flagged_at, hours: int = 24, after_hours: int = 0) -> dict:
    """Recent trajectory up to the flag, plus an optional separate slice after it."""
    rows = await connection.fetch(TRACK_HISTORY_QUERY, mmsi, flagged_at, hours)
    result: dict = {"positions": [dict(row) for row in rows]}
    if after_hours > 0:
        later = await connection.fetch(TRACK_AFTER_QUERY, mmsi, flagged_at, after_hours)
        result["after"] = [dict(row) for row in later]
    return result
