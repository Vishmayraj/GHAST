"""Known-zone spatial evidence query."""
from __future__ import annotations


JAMMING_ZONE_QUERY = """
SELECT name, confidence
FROM jamming_zones
WHERE active
  AND ST_Contains(
      zone::geometry,
      ST_SetSRID(ST_MakePoint($2, $1), 4326)
  )
  AND (first_seen IS NULL OR first_seen <= $3)
  AND (last_seen IS NULL OR last_seen >= $3)
LIMIT 1
"""


async def check_jamming_zones(connection, latitude: float, longitude: float, flagged_at) -> dict:
    """Check a geography zone using PostGIS's geometry containment function."""
    row = await connection.fetchrow(JAMMING_ZONE_QUERY, latitude, longitude, flagged_at)
    return {"matched": row is not None, "zone": dict(row) if row else None}
