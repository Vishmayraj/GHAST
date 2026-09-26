"""Known-zone spatial evidence query."""
from __future__ import annotations
async def check_jamming_zones(connection, latitude: float, longitude: float, flagged_at) -> dict:
    """Use geography containment so manual zones remain queryable without approximation."""
    row = await connection.fetchrow("SELECT name, confidence FROM jamming_zones WHERE active AND ST_Contains(zone, ST_SetSRID(ST_MakePoint($2,$1),4326)::geography) AND (first_seen IS NULL OR first_seen <= $3) AND (last_seen IS NULL OR last_seen >= $3) LIMIT 1", latitude, longitude, flagged_at)
    return {"matched": row is not None, "zone": dict(row) if row else None}
