from datetime import datetime, timedelta, timezone
import numpy as np
from features.pipeline import FeatureWindow
from training.train import train_model


def test_tiny_training_loop_reduces_loss() -> None:
    positions = np.array([(0.0, index * 0.001) for index in range(20)])
    window = FeatureWindow(1, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc), np.tile(np.array([10, 90, 90, 0, 70, 10, 0, 0], dtype=np.float32), (20, 1)), positions, tuple(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index) for index in range(20)))
    _, losses = train_model([window] * 3, epochs=10, learning_rate=0.01)
    assert losses[-1] < losses[0]
