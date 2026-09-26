"""Prior-incident evidence query."""
from __future__ import annotations
async def find_similar_incidents(connection, mmsi: int, anomaly_type: str) -> dict:
    """Early empty history is neutral evidence, not a reason to rule an event out."""
    rows = await connection.fetch("SELECT id, hypothesis, anomaly_score, flagged_at FROM incidents WHERE mmsi=$1 OR anomaly_type=$2 ORDER BY flagged_at DESC LIMIT 10", mmsi, anomaly_type)
    return {"similar_incidents": [dict(row) for row in rows]}
