from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from features.pipeline import FeatureWindow
from training.dataset_cache import _ShardWriter, is_validation_vessel, iterate_shard_batches, vessel_split_bucket


def _window(mmsi: int, seed: int) -> FeatureWindow:
    rng = np.random.default_rng(seed)
    features = rng.random((20, 8)).astype(np.float32)
    positions = np.cumsum(rng.random((20, 2)), axis=0)
    timestamps = tuple(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index) for index in range(20))
    return FeatureWindow(mmsi=mmsi, window_start=timestamps[0], window_end=timestamps[-1], features=features, positions=positions, timestamps=timestamps)


def test_vessel_split_is_deterministic_for_the_same_mmsi() -> None:
    assert vessel_split_bucket(205012345) == vessel_split_bucket(205012345)
    assert is_validation_vessel(205012345, 0.2) == is_validation_vessel(205012345, 0.2)


def test_vessel_split_is_roughly_the_requested_fraction() -> None:
    mmsis = range(200_000_000, 200_005_000)
    validation_count = sum(is_validation_vessel(mmsi, 0.2) for mmsi in mmsis)
    fraction = validation_count / len(list(mmsis))
    assert 0.15 < fraction < 0.25


def test_shard_writer_splits_into_shard_size_chunks(tmp_path: Path) -> None:
    writer = _ShardWriter(tmp_path / "train", shard_size=3)
    for index in range(7):
        writer.add(_window(mmsi=1, seed=index))
    writer.flush()
    assert writer.window_count == 7
    assert len(writer.shard_paths) == 3  # ceil(7 / 3)


def test_shard_batches_replay_every_window_with_matching_shapes(tmp_path: Path) -> None:
    writer = _ShardWriter(tmp_path / "train", shard_size=4)
    for index in range(10):
        writer.add(_window(mmsi=1, seed=index))
    writer.flush()

    total_windows = 0
    for features, classes, targets in iterate_shard_batches(writer.shard_paths, batch_size=3, shuffle=True, seed=7):
        assert features.shape[1:] == (20, 8)
        assert classes.shape[1:] == (20,)
        assert targets.shape[1:] == (19, 2)
        total_windows += features.shape[0]
    assert total_windows == 10
