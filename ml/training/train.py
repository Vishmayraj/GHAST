"""Train solely on presumed-clean live AIS windows, split by vessel."""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from datetime import datetime

import numpy as np
import torch
from torch import nn

from features.extract import N_FEATURES, VESSEL_CLASS_INDEX
from features.pipeline import FeatureWindow, load_training_windows
from models.bilstm.model import BiLSTMNextDelta

DEFAULT_LEARNING_RATE = 1e-3
DEFAULT_EPOCHS = 10
VALIDATION_FRACTION = 0.2


def split_by_vessel(windows: Sequence[FeatureWindow]) -> tuple[list[FeatureWindow], list[FeatureWindow]]:
    """Avoid optimistic validation scores from windows of a vessel already seen in training."""
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
    """Train an MSE next-delta objective; callers supply clean data only."""
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


async def run_training(dsn: str, start: datetime, end: datetime, epochs: int) -> tuple[BiLSTMNextDelta, list[float]]:
    """Boundary that loads live clean windows; synthetic injection never enters training."""
    return train_model(await load_training_windows(dsn, start, end), epochs=epochs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True); parser.add_argument("--start", required=True); parser.add_argument("--end", required=True)
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    args = parser.parse_args()
    model, losses = asyncio.run(run_training(args.dsn, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end), args.epochs))
    print(f"trained {type(model).__name__}; final loss={losses[-1]:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
