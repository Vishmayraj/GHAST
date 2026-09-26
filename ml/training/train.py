"""Train on AIS windows, split by vessel, streamed and mini-batched off disk.

The historical backfill (tens of millions of rows) never fits in RAM as
Python objects, tensors, or both at once. This module streams the requested
range through features.pipeline once, writes it to on-disk shards via
training.dataset_cache, then trains over those shards in configurable
mini-batches so peak memory stays bounded regardless of dataset size.
"""
from __future__ import annotations

import argparse
import asyncio
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from torch import nn

from features.extract import N_FEATURES, VESSEL_CLASS_INDEX
from features.pipeline import FeatureWindow, TrainingDataSource, estimate_row_count, load_live_coverage
from models.bilstm.model import BiLSTMNextDelta
from training.dataset_cache import CacheManifest, DEFAULT_VALIDATION_FRACTION, iterate_shard_batches, materialize_to_shards
from training.window_policy import initial_live_window, rolling_live_window

DEFAULT_LEARNING_RATE = 1e-3
DEFAULT_EPOCHS = 10
DEFAULT_BATCH_SIZE = 256
DEFAULT_CHECKPOINT_DIR = Path("checkpoints")
VALIDATION_FRACTION = DEFAULT_VALIDATION_FRACTION


def resolve_device(requested: str | None = None) -> torch.device:
    """Default to CUDA when it is available; never silently fall back once the caller asks for it."""
    if requested is not None:
        return torch.device(requested)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def split_by_vessel(windows: Sequence[FeatureWindow]) -> tuple[list[FeatureWindow], list[FeatureWindow]]:
    """Small in-memory vessel split, kept for callers that already hold a full window list."""
    vessel_ids = sorted({window.mmsi for window in windows})
    split = max(1, int(len(vessel_ids) * (1 - VALIDATION_FRACTION)))
    train_ids = set(vessel_ids[:split])
    return [window for window in windows if window.mmsi in train_ids], [window for window in windows if window.mmsi not in train_ids]


def _batch(windows: Sequence[FeatureWindow]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    features = torch.tensor(np.stack([window.features for window in windows]), dtype=torch.float32)
    classes = features[:, :, VESSEL_CLASS_INDEX].long()
    targets = torch.tensor(np.stack([np.diff(window.positions, axis=0) for window in windows]), dtype=torch.float32)
    return features, classes, targets


def train_model(windows: Sequence[FeatureWindow], epochs: int = DEFAULT_EPOCHS, learning_rate: float = DEFAULT_LEARNING_RATE) -> tuple[BiLSTMNextDelta, list[float]]:
    """Small-scale, single-batch training loop for a handful of windows already in memory.

    This is the path unit tests and quick manual checks use. Real training
    runs (see run_training/main below) go through the shard-cached,
    mini-batched path instead; that one scales to the historical dataset,
    this one does not and is not meant to.
    """
    if not windows:
        raise ValueError("training needs at least one clean trajectory window")
    model = BiLSTMNextDelta(N_FEATURES)
    optimiser = torch.optim.Adam(model.parameters(), lr=learning_rate)
    loss_fn = nn.MSELoss()
    features, classes, targets = _batch(windows)
    losses: list[float] = []
    for _ in range(epochs):
        optimiser.zero_grad()
        loss = loss_fn(model(features, classes)[:, :-1], targets)
        loss.backward(); optimiser.step()
        losses.append(float(loss.detach()))
    return model, losses


def _run_epoch(
    model: BiLSTMNextDelta, optimiser: torch.optim.Optimizer, loss_fn: nn.Module,
    shard_paths: list[Path], batch_size: int, device: torch.device, train: bool, label: str, shuffle_seed: int = 0,
) -> float:
    """One pass over a set of shards. Only the current mini-batch ever reaches the device."""
    model.train(mode=train)
    total_loss = 0.0
    total_windows = 0
    batch_count = 0
    for features_np, classes_np, targets_np in iterate_shard_batches(shard_paths, batch_size, shuffle=train, seed=shuffle_seed):
        features = torch.from_numpy(features_np).to(device)
        classes = torch.from_numpy(classes_np).to(device).long()
        targets = torch.from_numpy(targets_np).to(device)
        with torch.set_grad_enabled(train):
            predictions = model(features, classes)[:, :-1]
            loss = loss_fn(predictions, targets)
            if train:
                optimiser.zero_grad()
                loss.backward()
                optimiser.step()
        batch_count += 1
        total_windows += features.shape[0]
        total_loss += float(loss.detach()) * features.shape[0]
        if batch_count % 20 == 0:
            print(f"{label}: batch {batch_count} ({total_windows} windows), running loss={loss.item():.6f}")
    return total_loss / total_windows if total_windows else float("nan")


def train_from_shards(
    manifest: CacheManifest, epochs: int, learning_rate: float, batch_size: int,
    device: torch.device, checkpoint_dir: Path, resume_from: Path | None = None,
) -> tuple[BiLSTMNextDelta, list[float]]:
    """Mini-batch train/validate over shards already on disk, checkpointing every epoch."""
    if manifest.train_windows == 0:
        raise ValueError("training needs at least one clean trajectory window")
    model = BiLSTMNextDelta(N_FEATURES).to(device)
    optimiser = torch.optim.Adam(model.parameters(), lr=learning_rate)
    loss_fn = nn.MSELoss()
    start_epoch = 1
    if resume_from is not None:
        checkpoint = torch.load(resume_from, map_location=device)
        model.load_state_dict(checkpoint["model_state"])
        optimiser.load_state_dict(checkpoint["optimiser_state"])
        start_epoch = int(checkpoint["epoch"]) + 1
        print(f"resumed from {resume_from} at epoch {start_epoch}")

    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    train_losses: list[float] = []
    for epoch in range(start_epoch, start_epoch + epochs):
        # A different shuffle seed per epoch, otherwise "shuffled" batches would be
        # identical across epochs and mini-batch training would not gain anything
        # over training on one fixed pass through the data repeatedly.
        train_loss = _run_epoch(model, optimiser, loss_fn, manifest.train_shards, batch_size, device, True, f"epoch {epoch} train", shuffle_seed=epoch)
        val_loss = None
        if manifest.validation_shards:
            val_loss = _run_epoch(model, optimiser, loss_fn, manifest.validation_shards, batch_size, device, False, f"epoch {epoch} val")
        train_losses.append(train_loss)
        checkpoint = {
            "epoch": epoch, "model_state": model.state_dict(), "optimiser_state": optimiser.state_dict(),
            "train_loss": train_loss, "val_loss": val_loss,
        }
        epoch_path = checkpoint_dir / f"epoch_{epoch:03d}.pt"
        torch.save(checkpoint, epoch_path)
        torch.save(checkpoint, checkpoint_dir / "latest.pt")
        val_part = f" val_loss={val_loss:.6f}" if val_loss is not None else " val_loss=n/a (no validation vessels)"
        print(f"epoch {epoch} done: train_loss={train_loss:.6f}{val_part} checkpoint={epoch_path}")
    return model, train_losses


async def run_training(
    dsn: str, start: datetime, end: datetime, epochs: int, source: TrainingDataSource = "live",
    *,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    batch_size: int = DEFAULT_BATCH_SIZE,
    cache_dir: Path | None = None,
    checkpoint_dir: Path = DEFAULT_CHECKPOINT_DIR,
    max_vessels: int | None = None,
    max_windows: int | None = None,
    max_rows: int | None = None,
    device_name: str | None = None,
    resume_from: Path | None = None,
) -> tuple[BiLSTMNextDelta, list[float], Path]:
    """Stream one source to a shard cache, then mini-batch train over it; scales to 31M+ rows."""
    cache_dir = cache_dir or Path(tempfile.mkdtemp(prefix="ghast-training-cache-"))

    print(f"source={source} range={start.isoformat()}..{end.isoformat()}")
    row_estimate = await estimate_row_count(dsn, start, end, source)
    total_note = f"{row_estimate:,}" if row_estimate is not None else "unavailable (count timed out, continuing anyway)"
    print(f"estimated rows in range: {total_note}")

    def _progress(rows_seen: int, vessels_seen: int, windows_seen: int) -> None:
        denominator = f"/{row_estimate:,}" if row_estimate else ""
        print(f"materializing: rows={rows_seen:,}{denominator} vessels={vessels_seen:,} windows={windows_seen:,}")

    print(f"streaming into shard cache at {cache_dir}")
    manifest = await materialize_to_shards(
        dsn, start, end, source, cache_dir,
        max_vessels=max_vessels, max_windows=max_windows, max_rows=max_rows, on_progress=_progress,
    )
    print(
        f"materialized {manifest.train_windows:,} train windows and {manifest.validation_windows:,} "
        f"validation windows across {manifest.vessels_seen:,} vessels ({manifest.rows_seen:,} rows read)"
    )

    device = resolve_device(device_name)
    print(f"training on device={device}")
    model, losses = train_from_shards(manifest, epochs, learning_rate, batch_size, device, checkpoint_dir, resume_from)
    return model, losses, cache_dir


async def select_live_window(dsn: str, mode: str) -> tuple[datetime, datetime]:
    """Anchor initial training to first live data, then use rolling live coverage."""
    coverage = await load_live_coverage(dsn)
    decision = (
        initial_live_window(coverage.first_report, coverage.latest_report, datetime.now(timezone.utc))
        if mode == "initial" else rolling_live_window(coverage.first_report, coverage.latest_report)
    )
    if not decision.ready:
        raise ValueError(decision.reason)
    assert decision.start is not None and decision.end is not None
    return decision.start, decision.end


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--source", choices=("live", "historical"), default="live")
    parser.add_argument("--live-window", choices=("initial", "rolling"), default="initial")
    parser.add_argument("--start", help="Required only for explicit historical training.")
    parser.add_argument("--end", help="Required only for explicit historical training.")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--learning-rate", type=float, default=DEFAULT_LEARNING_RATE)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Windows per mini-batch. Lower this first if a laptop runs out of RAM/VRAM.")
    parser.add_argument("--cache-dir", help="Where to write shard cache; defaults to a fresh temp directory, printed at the start of the run.")
    parser.add_argument("--checkpoint-dir", default=str(DEFAULT_CHECKPOINT_DIR))
    parser.add_argument("--max-vessels", type=int, help="Development sampling: cap distinct vessels read.")
    parser.add_argument("--max-windows", type=int, help="Development sampling: cap total windows read.")
    parser.add_argument("--max-rows", type=int, help="Development sampling: cap raw AIS rows read.")
    parser.add_argument("--device", choices=("cpu", "cuda"), help="Defaults to cuda if available, else cpu.")
    parser.add_argument("--resume-from", help="Checkpoint .pt file to resume from.")
    args = parser.parse_args()
    if args.source == "live":
        start, end = asyncio.run(select_live_window(args.dsn, args.live_window))
    elif args.start and args.end:
        start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    else:
        parser.error("--start and --end are required when --source historical")
    model, losses, cache_dir = asyncio.run(run_training(
        args.dsn, start, end, args.epochs, args.source,
        learning_rate=args.learning_rate, batch_size=args.batch_size,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        checkpoint_dir=Path(args.checkpoint_dir),
        max_vessels=args.max_vessels, max_windows=args.max_windows, max_rows=args.max_rows,
        device_name=args.device, resume_from=Path(args.resume_from) if args.resume_from else None,
    ))
    print(f"trained {type(model).__name__}; final loss={losses[-1]:.6f}; cache={cache_dir}; checkpoints={args.checkpoint_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
