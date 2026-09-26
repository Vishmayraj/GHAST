from datetime import datetime, timedelta, timezone

import numpy as np

from features.extract import implied_speed_knots
from features.inject import MAX_VESSEL_SPEED_KNOTS, build_synthetic_dataset, inject_freeze_replay, inject_gradual_drift, inject_impossible_kinematics, inject_teleport_jump
from features.pipeline import FeatureWindow


def _window() -> FeatureWindow:
    count = 20
    return FeatureWindow(
        mmsi=123, window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=count - 1),
        features=np.tile(np.array([12, 90, 90, 0, 70, 12, 0, 0], dtype=np.float32), (count, 1)),
        positions=np.array([(10.0, 20.0 + index * 0.001) for index in range(count)]),
        timestamps=tuple(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index) for index in range(count)),
    )


def test_injection_build_is_byte_identical_for_a_seed() -> None:
    first = build_synthetic_dataset([_window()] * 8, seed=45)
    second = build_synthetic_dataset([_window()] * 8, seed=45)
    for left, right in zip(first, second):
        assert left.pattern == right.pattern
        assert left.is_spoofed == right.is_spoofed
        assert left.window.features.tobytes() == right.window.features.tobytes()
        assert left.window.positions.tobytes() == right.window.positions.tobytes()


def test_patterns_violate_their_intended_physical_signal() -> None:
    window = _window()
    teleported, teleport_labels = inject_teleport_jump(window, severity=1.0, seed=3)
    index = teleport_labels.index(True)
    previous = {"received_at": window.timestamps[index - 1], "latitude": window.positions[index - 1, 0], "longitude": window.positions[index - 1, 1]}
    current = {"received_at": window.timestamps[index], "latitude": teleported.positions[index, 0], "longitude": teleported.positions[index, 1]}
    assert implied_speed_knots(previous, current) > MAX_VESSEL_SPEED_KNOTS
    drifted, drift_labels = inject_gradual_drift(window, severity=0.8, seed=2)
    assert drift_labels.count(True) > 1 and not np.array_equal(drifted.positions, window.positions)
    frozen, freeze_labels = inject_freeze_replay(window, severity=1.0, seed=4)
    assert freeze_labels.count(True) >= 1 and any(np.array_equal(frozen.positions[index], frozen.positions[index - 1]) for index in range(1, len(frozen.positions)))
    turning, kinematic_labels = inject_impossible_kinematics(window, severity=1.0, seed=5)
    changed = kinematic_labels.index(True)
    assert turning.features[changed, 3] > 100
