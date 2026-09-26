"""Trajectory evidence query."""
from __future__ import annotations
async def get_track_history(connection, mmsi: int, flagged_at, hours: int = 24) -> dict:
    """Keep windowed and full recent trajectory evidence available for human audit."""
    rows = await connection.fetch("SELECT received_at, latitude, longitude, sog_knots, cog_deg FROM vessel_position WHERE mmsi=$1 AND received_at BETWEEN $2 - ($3::text || ' hours')::interval AND $2 + ($3::text || ' hours')::interval ORDER BY received_at", mmsi, flagged_at, hours)
    return {"positions": [dict(row) for row in rows]}
