"""Fetch and window clean AIS trajectories for training and synthetic evaluation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

import asyncpg
import numpy as np

from .extract import extract_features

MINIMUM_REPORTS_PER_VESSEL = 20
WINDOW_LENGTH = MINIMUM_REPORTS_PER_VESSEL


@dataclass(frozen=True)
class FeatureWindow:
    """A model-ready sequence plus coordinates retained for delta targets/injection."""

    mmsi: int
    window_start: datetime
    window_end: datetime
    features: np.ndarray
    positions: np.ndarray
    timestamps: tuple[datetime, ...]


POSITION_QUERY = """
SELECT vp.received_at, vp.mmsi, vp.latitude, vp.longitude, vp.sog_knots,
       vp.cog_deg, vp.true_heading_deg, vp.rate_of_turn, vp.navigational_status,
       vs.ship_type
FROM vessel_position AS vp
LEFT JOIN vessel_static AS vs ON vs.mmsi = vp.mmsi
WHERE vp.received_at >= $1 AND vp.received_at < $2
ORDER BY vp.mmsi, vp.received_at
"""


def window_rows(rows: Iterable[Mapping[str, object]], window_length: int = WINDOW_LENGTH) -> list[FeatureWindow]:
    """Make non-overlapping windows; this simple stride avoids correlated duplicates in v1."""
    if window_length < MINIMUM_REPORTS_PER_VESSEL:
        raise ValueError(f"window_length must be at least {MINIMUM_REPORTS_PER_VESSEL}")
    grouped: dict[int, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["mmsi"])].append(row)

    windows: list[FeatureWindow] = []
    for mmsi, vessel_rows in grouped.items():
        ordered = sorted(vessel_rows, key=lambda row: row["received_at"])
        if len(ordered) < MINIMUM_REPORTS_PER_VESSEL:
            continue
        ship_type = ordered[0].get("ship_type")
        for start in range(0, len(ordered) - window_length + 1, window_length):
            chunk = ordered[start : start + window_length]
            windows.append(FeatureWindow(
                mmsi=mmsi,
                window_start=chunk[0]["received_at"],  # type: ignore[arg-type]
                window_end=chunk[-1]["received_at"],  # type: ignore[arg-type]
                features=extract_features(chunk, int(ship_type) if ship_type is not None else None),
                positions=np.asarray([(float(row["latitude"]), float(row["longitude"])) for row in chunk], dtype=np.float64),
                timestamps=tuple(row["received_at"] for row in chunk),  # type: ignore[arg-type]
            ))
    return windows


async def fetch_training_windows(connection: asyncpg.Connection, start: datetime, end: datetime) -> list[FeatureWindow]:
    """Read once, then delegate all grouping/windowing to the pure function above."""
    records = await connection.fetch(POSITION_QUERY, start, end)
    return window_rows([dict(record) for record in records])


async def load_training_windows(dsn: str, start: datetime, end: datetime) -> list[FeatureWindow]:
    """Open the database only at this boundary so callers can inject a test connection."""
    connection = await asyncpg.connect(dsn)
    try:
        return await fetch_training_windows(connection, start, end)
    finally:
        await connection.close()
