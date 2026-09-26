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
    with torch.no_grad():
        features = torch.from_numpy(window.features).unsqueeze(0).float()
        classes = torch.from_numpy(window.features[:, VESSEL_CLASS_INDEX].astype(np.int64)).unsqueeze(0)
        predicted = model(features, classes).squeeze(0).cpu().numpy()
    actual = np.diff(window.positions, axis=0)
    errors = np.zeros(len(window.positions), dtype=np.float64)
    errors[1:] = np.linalg.norm(predicted[:-1] - actual, axis=1)
    return errors
