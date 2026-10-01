"""Prior-incident evidence queries.

Two different questions with two different meanings, so they are two result keys:

* `same_vessel`: earlier incidents for this MMSI. A vessel with prior incidents is
  evidence against a one-off targeted event.
* `same_pattern_elsewhere`: earlier incidents with the same `anomaly_type` on other
  vessels. This is fleet-level context and says nothing about this vessel's own history.
"""
from __future__ import annotations

SAME_VESSEL_QUERY = """
SELECT id, hypothesis, anomaly_score, anomaly_type, flagged_at
FROM incidents
WHERE mmsi = $1
ORDER BY flagged_at DESC
LIMIT 10
"""

SAME_PATTERN_ELSEWHERE_QUERY = """
SELECT id, mmsi, hypothesis, anomaly_score, flagged_at
FROM incidents
WHERE mmsi <> $1 AND anomaly_type = $2
ORDER BY flagged_at DESC
LIMIT 10
"""


async def find_similar_incidents(connection, mmsi: int, anomaly_type: str) -> dict:
    """Early empty history is neutral evidence, not a reason to rule an event out."""
    same_vessel = await connection.fetch(SAME_VESSEL_QUERY, mmsi)
    elsewhere = await connection.fetch(SAME_PATTERN_ELSEWHERE_QUERY, mmsi, anomaly_type)
    return {
        "same_vessel": [dict(row) for row in same_vessel],
        "same_pattern_elsewhere": [dict(row) for row in elsewhere],
    }
