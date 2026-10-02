"""Fleet clustering: one vessel, or many vessels in one place at once?

Pure functions, numpy only. DBSCAN over vessel positions with the haversine distance. A report
is one vessel's flagged or anomalous position at a time. Vessels are what get counted, never
reports: within a time bucket each vessel contributes one point (its latest report), and a
cluster needs `min_vessels` distinct vessels. That way one vessel flagging ten times in an hour
is not a cluster.

It is plain numpy because the inputs are tens of points (one bucket, one area) and the scorer
image should not grow a scikit-learn install for that. `dbscan()` is the textbook algorithm,
checked against hand-made layouts in `tests`.

All three parameters are uncalibrated starting values. Tuning them needs live and historical
rows and no labels exist, so the only honest outputs are counts such as clusters per day and
vessels per cluster, never an accuracy figure (ImplementationPlans/03).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import numpy as np

# Uncalibrated. Roughly the reach of one jammer on open water. Revisit with clusters-per-day
# and vessels-per-cluster counts from real data.
EPS_KM = 25.0
# Uncalibrated. Three vessels: two agreeing could be a coincidence, three is a pattern.
MIN_VESSELS = 3
# Uncalibrated. Interference events are short; a bucket this wide keeps one event together.
BUCKET_MINUTES = 30

EARTH_RADIUS_KM = 6371.0088
NOISE = -1


def haversine_matrix_km(latitudes: np.ndarray, longitudes: np.ndarray) -> np.ndarray:
    """Pairwise great-circle distances in km. Safe across the antimeridian."""
    lat, lon = np.radians(latitudes), np.radians(longitudes)
    dlat = lat[:, None] - lat[None, :]
    dlon = lon[:, None] - lon[None, :]
    a = np.sin(dlat / 2) ** 2 + np.cos(lat)[:, None] * np.cos(lat)[None, :] * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def dbscan(distances: np.ndarray, eps: float, min_samples: int) -> list[int]:
    """Textbook DBSCAN on a precomputed distance matrix. Returns one label per point, -1 is noise.

    A point is a core point when at least `min_samples` points (itself included) lie within
    `eps`. Clusters grow from core points through their neighbours; a non-core point within eps
    of a core point joins that cluster as a border point.
    """
    count = len(distances)
    labels = [NOISE] * count
    visited = [False] * count
    neighbours = [np.flatnonzero(distances[i] <= eps).tolist() for i in range(count)]
    cluster = 0
    for start in range(count):
        if visited[start]:
            continue
        visited[start] = True
        if len(neighbours[start]) < min_samples:
            continue  # noise for now; a later cluster may still claim it as a border point
        labels[start] = cluster
        queue = list(neighbours[start])
        while queue:
            point = queue.pop()
            if labels[point] == NOISE:
                labels[point] = cluster
            if visited[point]:
                continue
            visited[point] = True
            if len(neighbours[point]) >= min_samples:
                queue.extend(neighbours[point])
        cluster += 1
    return labels


def _bucket_key(moment: datetime, bucket_minutes: float | None) -> int:
    if bucket_minutes is None:
        return 0
    return int(moment.timestamp() // (bucket_minutes * 60))


def cluster_reports(
    reports: Sequence[Mapping[str, Any]],
    eps_km: float = EPS_KM,
    min_vessels: int = MIN_VESSELS,
    bucket_minutes: float | None = BUCKET_MINUTES,
) -> list[dict[str, Any]]:
    """Group reports (`mmsi`, `time`, `lat`, `lon`) into clusters of distinct vessels.

    Reports are bucketed by time (`bucket_minutes=None` puts everything in one bucket), each
    vessel keeps its latest report per bucket, and DBSCAN runs per bucket with
    `min_samples = min_vessels`. Returns clusters sorted by bucket then size, each
    `{"bucket": int, "vessels": [mmsi, ...], "n_vessels": int, "latitude", "longitude", "radius_km"}`.
    The centroid is the mean of the member positions and `radius_km` is the farthest member from it.
    """
    if min_vessels < 1 or eps_km <= 0:
        raise ValueError("min_vessels must be at least 1 and eps_km positive")
    buckets: dict[int, dict[Any, Mapping[str, Any]]] = {}
    for report in reports:
        latest = buckets.setdefault(_bucket_key(report["time"], bucket_minutes), {})
        held = latest.get(report["mmsi"])
        if held is None or report["time"] > held["time"]:
            latest[report["mmsi"]] = report
    clusters: list[dict[str, Any]] = []
    for bucket in sorted(buckets):
        members = list(buckets[bucket].values())
        if len(members) < min_vessels:
            continue
        lat = np.array([m["lat"] for m in members], dtype=float)
        lon = np.array([m["lon"] for m in members], dtype=float)
        labels = dbscan(haversine_matrix_km(lat, lon), eps_km, min_vessels)
        for label in sorted(set(labels) - {NOISE}):
            index = [i for i, value in enumerate(labels) if value == label]
            centre_lat, centre_lon = float(lat[index].mean()), float(lon[index].mean())
            spread = haversine_matrix_km(np.append(lat[index], centre_lat), np.append(lon[index], centre_lon))[-1, :-1]
            clusters.append({
                "bucket": bucket, "vessels": sorted(members[i]["mmsi"] for i in index), "n_vessels": len(index),
                "latitude": centre_lat, "longitude": centre_lon, "radius_km": float(spread.max()),
            })
    clusters.sort(key=lambda c: (c["bucket"], -c["n_vessels"]))
    return clusters


def cluster_containing(
    mmsi: Any,
    reports: Sequence[Mapping[str, Any]],
    eps_km: float = EPS_KM,
    min_vessels: int = MIN_VESSELS,
) -> dict[str, Any] | None:
    """The cluster (one time bucket, all `reports` taken as simultaneous) that holds `mmsi`, or None."""
    for cluster in cluster_reports(reports, eps_km, min_vessels, bucket_minutes=None):
        if mmsi in cluster["vessels"]:
            return cluster
    return None
