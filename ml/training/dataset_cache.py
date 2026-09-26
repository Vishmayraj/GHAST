"""Materialise streamed feature windows to disk in shards, then replay them in mini-batches.

The historical backfill is too large to keep as one Python object graph, and
it is also too large to re-query from TimescaleDB once per training epoch.
This module sits between the two: it drains features.pipeline.stream_feature_windows
exactly once, splits vessels into train/validation deterministically by MMSI
(no need to know the full vessel list up front, so the split works while
still streaming), and writes small .npz shards to cache_dir. Training then
reads one shard at a time back off disk, so peak memory is one shard, not
one dataset.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from features.extract import VESSEL_CLASS_INDEX
from features.pipeline import FeatureWindow, ProgressCallback, TrainingDataSource, stream_feature_windows

DEFAULT_VALIDATION_FRACTION = 0.2
DEFAULT_SHARD_SIZE = 2048
DEFAULT_SPLIT_BUCKETS = 1000


def vessel_split_bucket(mmsi: int, buckets: int = DEFAULT_SPLIT_BUCKETS) -> int:
    """Stable bucket in [0, buckets) from MMSI alone, so the split needs no vessel list up front."""
    digest = hashlib.sha256(str(int(mmsi)).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % buckets


def is_validation_vessel(
    mmsi: int, validation_fraction: float = DEFAULT_VALIDATION_FRACTION, buckets: int = DEFAULT_SPLIT_BUCKETS,
) -> bool:
    """Assign a whole vessel to validation or train; a vessel never appears on both sides."""
    threshold = int(buckets * (1 - validation_fraction))
    return vessel_split_bucket(mmsi, buckets) >= threshold


@dataclass
class _ShardWriter:
    """Buffers windows up to shard_size, then writes one .npz and drops the buffer."""

    directory: Path
    shard_size: int
    _features: list[np.ndarray] = field(default_factory=list)
    _classes: list[np.ndarray] = field(default_factory=list)
    _targets: list[np.ndarray] = field(default_factory=list)
    _shard_index: int = 0
    _window_count: int = 0
    shard_paths: list[Path] = field(default_factory=list)

    def add(self, window: FeatureWindow) -> None:
        self._features.append(window.features)
        self._classes.append(window.features[:, VESSEL_CLASS_INDEX].astype(np.int64))
        self._targets.append(np.diff(window.positions, axis=0).astype(np.float32))
        self._window_count += 1
        if len(self._features) >= self.shard_size:
            self.flush()

    def flush(self) -> None:
        if not self._features:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"shard_{self._shard_index:06d}.npz"
        np.savez(
            path,
            features=np.stack(self._features).astype(np.float32),
            classes=np.stack(self._classes),
            targets=np.stack(self._targets),
        )
        self.shard_paths.append(path)
        self._shard_index += 1
        self._features.clear()
        self._classes.clear()
        self._targets.clear()

    @property
    def window_count(self) -> int:
        return self._window_count


@dataclass(frozen=True)
class CacheManifest:
    """What materialize_to_shards produced, enough to train and to report progress."""

    cache_dir: Path
    train_shards: list[Path]
    validation_shards: list[Path]
    train_windows: int
    validation_windows: int
    vessels_seen: int
    rows_seen: int


async def materialize_to_shards(
    dsn: str,
    start: datetime,
    end: datetime,
    source: TrainingDataSource,
    cache_dir: Path | str,
    *,
    shard_size: int = DEFAULT_SHARD_SIZE,
    validation_fraction: float = DEFAULT_VALIDATION_FRACTION,
    max_vessels: int | None = None,
    max_windows: int | None = None,
    max_rows: int | None = None,
    progress_every: int = 200_000,
    on_progress: ProgressCallback | None = None,
) -> CacheManifest:
    """Stream one source once and write it out as train/validation shards on disk."""
    cache_dir = Path(cache_dir)
    train_writer = _ShardWriter(cache_dir / "train", shard_size)
    validation_writer = _ShardWriter(cache_dir / "validation", shard_size)
    counts = {"rows": 0, "vessels": 0, "windows": 0}

    def _record_progress(rows_seen: int, vessels_seen: int, windows_seen: int) -> None:
        counts["rows"], counts["vessels"], counts["windows"] = rows_seen, vessels_seen, windows_seen
        if on_progress is not None:
            on_progress(rows_seen, vessels_seen, windows_seen)

    async for window in stream_feature_windows(
        dsn, start, end, source,
        max_vessels=max_vessels, max_windows=max_windows, max_rows=max_rows,
        progress_every=progress_every, on_progress=_record_progress,
    ):
        if is_validation_vessel(window.mmsi, validation_fraction):
            validation_writer.add(window)
        else:
            train_writer.add(window)

    train_writer.flush()
    validation_writer.flush()

    return CacheManifest(
        cache_dir=cache_dir,
        train_shards=train_writer.shard_paths,
        validation_shards=validation_writer.shard_paths,
        train_windows=train_writer.window_count,
        validation_windows=validation_writer.window_count,
        vessels_seen=counts["vessels"],
        rows_seen=counts["rows"],
    )


def iterate_shard_batches(
    shard_paths: list[Path], batch_size: int, shuffle: bool = True, seed: int = 0,
) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Yield (features, classes, targets) mini-batches, one shard resident at a time.

    Shard order is shuffled per call when shuffle=True so consecutive epochs
    do not see vessels in the same order, but only one shard's arrays are
    ever loaded into memory at once.
    """
    paths = list(shard_paths)
    order_rng = random.Random(seed)
    if shuffle:
        order_rng.shuffle(paths)
    for path in paths:
        with np.load(path) as data:
            features = data["features"]
            classes = data["classes"]
            targets = data["targets"]
        row_order = np.arange(features.shape[0])
        if shuffle:
            np.random.default_rng(order_rng.randrange(2**32)).shuffle(row_order)
        for start in range(0, len(row_order), batch_size):
            batch_index = row_order[start : start + batch_size]
            yield features[batch_index], classes[batch_index], targets[batch_index]
        del features, classes, targets
