"""Turn already-fetched AIS reports into model features without performing I/O.

Keeping this module pure makes the feature contract testable independently of
TimescaleDB and prevents an accidental query-per-report training path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

import numpy as np

KNOTS_PER_KILOMETRE_PER_HOUR = 0.539956803
MISSING_VALUE = -1.0

# The masks deliberately accompany sentinel values: zero is meaningful for
# heading and rate of turn, whereas -1 alone would make missingness implicit.
SOG_INDEX = 0
COG_INDEX = 1
HEADING_INDEX = 2
RATE_OF_TURN_INDEX = 3
VESSEL_CLASS_INDEX = 4
IMPLIED_SPEED_INDEX = 5
HEADING_MISSING_INDEX = 6
RATE_OF_TURN_MISSING_INDEX = 7
N_FEATURES = 8


def _as_datetime(value: datetime | str) -> datetime:
    """Accept database datetimes and ISO strings so fixtures use production shape."""
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def haversine_kilometres(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Return great-circle distance, avoiding planar assumptions across AIS regions."""
    earth_radius_km = 6371.0088
    lat_a, lon_a, lat_b, lon_b = np.radians([latitude_a, longitude_a, latitude_b, longitude_b])
    delta_lat = lat_b - lat_a
    delta_lon = lon_b - lon_a
    arc = np.sin(delta_lat / 2) ** 2 + np.cos(lat_a) * np.cos(lat_b) * np.sin(delta_lon / 2) ** 2
    return float(2 * earth_radius_km * np.arcsin(np.sqrt(arc)))


def implied_speed_knots(previous: Mapping[str, object], current: Mapping[str, object]) -> float:
    """Compute motion from report timestamps because AIS delivery is irregular."""
    elapsed_seconds = (_as_datetime(current["received_at"]) - _as_datetime(previous["received_at"])).total_seconds()
    if elapsed_seconds <= 0:
        return MISSING_VALUE
    distance_km = haversine_kilometres(
        float(previous["latitude"]), float(previous["longitude"]),
        float(current["latitude"]), float(current["longitude"]),
    )
    return distance_km * 3600 / elapsed_seconds * KNOTS_PER_KILOMETRE_PER_HOUR


def _value_and_mask(value: object | None) -> tuple[float, float]:
    if value is None:
        return MISSING_VALUE, 1.0
    return float(value), 0.0


def extract_features(rows: Sequence[Mapping[str, object]], ship_type: int | None) -> np.ndarray:
    """Build one feature vector per chronologically ordered position report.

    The caller supplies vessel class from ``vessel_static`` because feature
    extraction must remain usable for fixtures and offline replay without DB access.
    """
    features = np.empty((len(rows), N_FEATURES), dtype=np.float32)
    vessel_class = float(ship_type) if ship_type is not None else MISSING_VALUE
    for index, row in enumerate(rows):
        heading, heading_missing = _value_and_mask(row.get("true_heading_deg"))
        rate_of_turn, rate_of_turn_missing = _value_and_mask(row.get("rate_of_turn"))
        features[index] = (
            float(row["sog_knots"]) if row.get("sog_knots") is not None else MISSING_VALUE,
            float(row["cog_deg"]) if row.get("cog_deg") is not None else MISSING_VALUE,
            heading,
            rate_of_turn,
            vessel_class,
            MISSING_VALUE if index == 0 else implied_speed_knots(rows[index - 1], row),
            heading_missing,
            rate_of_turn_missing,
        )
    return features
