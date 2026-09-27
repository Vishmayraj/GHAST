"""Prediction-error conversion shared by evaluation and scoring callers."""
from __future__ import annotations

import numpy as np
import torch

from features.extract import VESSEL_CLASS_INDEX
from features.pipeline import FeatureWindow
from .model import BiLSTMNextDelta


def prediction_errors(model: BiLSTMNextDelta, window: FeatureWindow) -> np.ndarray:
    """Score each transition; the first report has no preceding prediction and is zero."""
    model.eval()
    device = next(model.parameters()).device

    with torch.no_grad():
        features = (
            torch.from_numpy(window.features)
            .unsqueeze(0)
            .float()
            .to(device)
        )
        classes = (
            torch.from_numpy(
                window.features[:, VESSEL_CLASS_INDEX].astype(np.int64)
            )
            .unsqueeze(0)
            .to(device)
        )

        predicted = model(features, classes).squeeze(0).cpu().numpy()

    actual = np.diff(window.positions, axis=0)
    errors = np.zeros(len(window.positions), dtype=np.float64)
    errors[1:] = np.linalg.norm(predicted[:-1] - actual, axis=1)
    return errors


def prediction_errors_batch(model: BiLSTMNextDelta, windows: list[FeatureWindow]) -> list[np.ndarray]:
    """Score a same-length window batch in one model forward pass.

    The evaluator uses this to keep the GPU busy.  The single-window helper
    above remains the lightweight API for online callers.
    """
    if not windows:
        return []
    lengths = {len(window.positions) for window in windows}
    if len(lengths) != 1:
        raise ValueError("batched prediction errors require equally sized windows")

    model.eval()
    device = next(model.parameters()).device
    with torch.no_grad():
        features_np = np.stack([window.features for window in windows])
        features = torch.from_numpy(features_np).float().to(device)
        classes = torch.from_numpy(features_np[:, :, VESSEL_CLASS_INDEX].astype(np.int64)).to(device)
        predicted = model(features, classes).cpu().numpy()

    actual = np.stack([np.diff(window.positions, axis=0) for window in windows])
    errors = np.zeros((len(windows), len(windows[0].positions)), dtype=np.float64)
    errors[:, 1:] = np.linalg.norm(predicted[:, :-1] - actual, axis=2)
    return [errors[index] for index in range(len(windows))]
