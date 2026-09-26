"""Fetch and window clean AIS trajectories for training and synthetic evaluation.

The historical MarineCadastre backfill is tens of millions of rows, so this
module never materialises a full range with connection.fetch(). Every read
goes through a server-side cursor and windows are produced incrementally,
vessel by vessel, as rows arrive in (mmsi, received_at) order. Callers that
still want a plain list (the evaluation harness, small live windows) get one
back from fetch_training_windows/load_training_windows, but the list is built
by draining the same streaming generator rather than a second code path.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import asyncpg
import numpy as np

from .extract import extract_features

MINIMUM_REPORTS_PER_VESSEL = 20
WINDOW_LENGTH = MINIMUM_REPORTS_PER_VESSEL
TrainingDataSource = Literal["live", "historical"]

# Rows fetched per network round trip while iterating the server-side
# cursor. Bounds memory to roughly this many raw rows at a time, not the
# whole result set.
DEFAULT_PREFETCH = 2000
DEFAULT_PROGRESS_EVERY = 200_000
DEFAULT_COUNT_TIMEOUT_MS = 15_000

ProgressCallback = Callable[[int, int, int], None]


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
  AND vp.message_type IS DISTINCT FROM 'historical'
ORDER BY vp.mmsi, vp.received_at
"""

HISTORICAL_POSITION_QUERY = POSITION_QUERY.replace(
    "vp.message_type IS DISTINCT FROM 'historical'", "vp.message_type = 'historical'"
)

LIVE_COVERAGE_QUERY = """
SELECT min(received_at) AS first_report, max(received_at) AS latest_report
FROM vessel_position WHERE message_type IS DISTINCT FROM 'historical'
"""

# Plain row counts against vessel_position only, no join, so a slow or
# cancelled estimate never risks the same memory blowup it is meant to warn
# callers about ahead of the actual streamed read.
COUNT_QUERY = """
SELECT count(*) FROM vessel_position AS vp
WHERE vp.received_at >= $1 AND vp.received_at < $2
  AND vp.message_type IS DISTINCT FROM 'historical'
"""

HISTORICAL_COUNT_QUERY = COUNT_QUERY.replace(
    "vp.message_type IS DISTINCT FROM 'historical'", "vp.message_type = 'historical'"
)


@dataclass(frozen=True)
class SourceCoverage:
    """Source-scoped bounds prevent imported records from masquerading as live data."""

    first_report: datetime | None
    latest_report: datetime | None


def _windows_for_vessel(
    mmsi: int, ordered_rows: list[Mapping[str, object]], window_length: int = WINDOW_LENGTH
) -> list[FeatureWindow]:
    """Build windows for one vessel's reports, already time-ordered by the caller."""
    if len(ordered_rows) < MINIMUM_REPORTS_PER_VESSEL:
        return []
    ship_type = ordered_rows[0].get("ship_type")
    windows: list[FeatureWindow] = []
    for start in range(0, len(ordered_rows) - window_length + 1, window_length):
        chunk = ordered_rows[start : start + window_length]
        windows.append(FeatureWindow(
            mmsi=mmsi,
            window_start=chunk[0]["received_at"],  # type: ignore[arg-type]
            window_end=chunk[-1]["received_at"],  # type: ignore[arg-type]
            features=extract_features(chunk, int(ship_type) if ship_type is not None else None),
            positions=np.asarray([(float(row["latitude"]), float(row["longitude"])) for row in chunk], dtype=np.float64),
            timestamps=tuple(row["received_at"] for row in chunk),  # type: ignore[arg-type]
        ))
    return windows


def window_rows(rows: Iterable[Mapping[str, object]], window_length: int = WINDOW_LENGTH) -> list[FeatureWindow]:
    """Make non-overlapping windows; this simple stride avoids correlated duplicates in v1.

    In-memory helper for small fixtures/tests. Anything reading from the
    database goes through the streaming path below instead.
    """
    if window_length < MINIMUM_REPORTS_PER_VESSEL:
        raise ValueError(f"window_length must be at least {MINIMUM_REPORTS_PER_VESSEL}")
    grouped: dict[int, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["mmsi"])].append(row)

    windows: list[FeatureWindow] = []
    for mmsi, vessel_rows in grouped.items():
        ordered = sorted(vessel_rows, key=lambda row: row["received_at"])
        windows.extend(_windows_for_vessel(mmsi, ordered, window_length))
    return windows


async def _stream_rows_from_connection(
    connection: asyncpg.Connection,
    start: datetime,
    end: datetime,
    source: TrainingDataSource = "live",
    window_length: int = WINDOW_LENGTH,
    max_vessels: int | None = None,
    max_windows: int | None = None,
    max_rows: int | None = None,
    prefetch: int = DEFAULT_PREFETCH,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    on_progress: ProgressCallback | None = None,
) -> AsyncIterator[FeatureWindow]:
    """Stream one source through a server-side cursor and yield windows as vessels complete.

    The query orders by (mmsi, received_at), so a vessel's rows always arrive
    contiguously; a window can be emitted the moment a vessel's run of rows
    ends, without ever holding more than one vessel's reports in memory.
    """
    query = POSITION_QUERY if source == "live" else HISTORICAL_POSITION_QUERY
    current_mmsi: int | None = None
    buffer: list[Mapping[str, object]] = []
    rows_seen = 0
    vessels_seen = 0
    windows_yielded = 0

    async with connection.transaction():
        async for record in connection.cursor(query, start, end, prefetch=prefetch):
            row = dict(record)
            mmsi = int(row["mmsi"])
            if current_mmsi is not None and mmsi != current_mmsi:
                for window in _windows_for_vessel(current_mmsi, buffer, window_length):
                    yield window
                    windows_yielded += 1
                    if max_windows is not None and windows_yielded >= max_windows:
                        return
                buffer = []
                vessels_seen += 1
                if max_vessels is not None and vessels_seen >= max_vessels:
                    return
            current_mmsi = mmsi
            buffer.append(row)
            rows_seen += 1
            if on_progress is not None and rows_seen % progress_every == 0:
                on_progress(rows_seen, vessels_seen, windows_yielded)
            if max_rows is not None and rows_seen >= max_rows:
                break

    if current_mmsi is not None:
        for window in _windows_for_vessel(current_mmsi, buffer, window_length):
            yield window
            windows_yielded += 1
            if max_windows is not None and windows_yielded >= max_windows:
                return
        vessels_seen += 1
    if on_progress is not None:
        on_progress(rows_seen, vessels_seen, windows_yielded)


async def stream_feature_windows(
    dsn: str,
    start: datetime,
    end: datetime,
    source: TrainingDataSource = "live",
    *,
    window_length: int = WINDOW_LENGTH,
    max_vessels: int | None = None,
    max_windows: int | None = None,
    max_rows: int | None = None,
    prefetch: int = DEFAULT_PREFETCH,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    on_progress: ProgressCallback | None = None,
) -> AsyncIterator[FeatureWindow]:
    """Open a connection for the caller and stream windows for one source/date range.

    max_vessels/max_windows/max_rows are explicit development sampling knobs;
    leave them unset for a real training run so nothing is silently truncated.
    """
    connection = await asyncpg.connect(dsn)
    try:
        async for window in _stream_rows_from_connection(
            connection, start, end, source, window_length,
            max_vessels, max_windows, max_rows, prefetch, progress_every, on_progress,
        ):
            yield window
    finally:
        await connection.close()


async def fetch_training_windows(
    connection: asyncpg.Connection, start: datetime, end: datetime,
    source: TrainingDataSource = "live",
) -> list[FeatureWindow]:
    """Read one explicitly selected source, then prepare pure feature windows.

    Kept list-returning for existing callers (the evaluation harness reads
    small live windows this way); internally this drains the same streaming
    cursor as the large-scale historical path, so a caller who points this at
    a huge range is still memory-bounded row by row rather than crashing.
    """
    return [window async for window in _stream_rows_from_connection(connection, start, end, source)]


async def load_training_windows(
    dsn: str, start: datetime, end: datetime, source: TrainingDataSource = "live",
) -> list[FeatureWindow]:
    """Open the database only at this boundary so callers can inject a test connection."""
    connection = await asyncpg.connect(dsn)
    try:
        return await fetch_training_windows(connection, start, end, source)
    finally:
        await connection.close()


async def fetch_live_coverage(connection: asyncpg.Connection) -> SourceCoverage:
    """Discover live bounds without relying on wall-clock-relative calendar dates."""
    record = await connection.fetchrow(LIVE_COVERAGE_QUERY)
    return SourceCoverage(record["first_report"], record["latest_report"])


async def load_live_coverage(dsn: str) -> SourceCoverage:
    """Open the database only at this I/O boundary, like window loading above."""
    connection = await asyncpg.connect(dsn)
    try:
        return await fetch_live_coverage(connection)
    finally:
        await connection.close()


async def estimate_row_count(
    dsn: str, start: datetime, end: datetime, source: TrainingDataSource = "live",
    timeout_ms: int = DEFAULT_COUNT_TIMEOUT_MS,
) -> int | None:
    """Best-effort row count for progress reporting, never for gating the read itself.

    A count(*) over tens of millions of rows can still be slow, so this runs
    under a server-side statement_timeout and returns None rather than
    hanging if it does not come back in time; callers must treat None as
    "no total available" and keep going without a percentage.
    """
    query = COUNT_QUERY if source == "live" else HISTORICAL_COUNT_QUERY
    connection = await asyncpg.connect(dsn)
    try:
        async with connection.transaction():
            await connection.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            try:
                return await connection.fetchval(query, start, end)
            except asyncpg.exceptions.QueryCanceledError:
                return None
    finally:
        await connection.close()
