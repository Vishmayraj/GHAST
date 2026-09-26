"""Reproducible synthetic spoof injection over clean, real-shaped trajectories.

The injectors mutate copies only. This lets one clean window supply a normal
control and multiple labeled counterfactuals without contaminating training data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .extract import COG_INDEX, RATE_OF_TURN_INDEX
from .pipeline import FeatureWindow

MIN_SEVERITY = 0.0
MAX_SEVERITY = 1.0
CONTROL_FRACTION = 0.25
MIN_TELEPORT_KILOMETRES = 10.0
MAX_TELEPORT_KILOMETRES = 100.0
DEGREES_PER_KILOMETRE = 1 / 111.0
MAX_VESSEL_SPEED_KNOTS = 40.0
SPOOF_PATTERNS = (
    "teleport_jump",
    "gradual_drift",
    "freeze_replay",
    "impossible_kinematics",
)


@dataclass(frozen=True)
class InjectedWindow:
    """A trajectory whose labels identify reports altered by one counterfactual."""

    window: FeatureWindow
    is_spoofed: tuple[bool, ...]
    pattern: str | None


def _validate_severity(severity: float) -> None:
    if not MIN_SEVERITY <= severity <= MAX_SEVERITY:
        raise ValueError(f"severity must be between {MIN_SEVERITY} and {MAX_SEVERITY}")


def _copy_window(window: FeatureWindow, features: np.ndarray, positions: np.ndarray) -> FeatureWindow:
    return FeatureWindow(window.mmsi, window.window_start, window.window_end, features, positions, window.timestamps)


def _target_index(window: FeatureWindow, rng: np.random.Generator) -> int:
    if len(window.positions) < 2:
        raise ValueError("a spoof injection needs at least two position reports")
    return int(rng.integers(1, len(window.positions)))


def inject_teleport_jump(window: FeatureWindow, severity: float, seed: int) -> tuple[FeatureWindow, list[bool]]:
    """Displace one report far enough to imply speed above a conservative class limit."""
    _validate_severity(severity)
    rng = np.random.default_rng(seed)
    positions = window.positions.copy()
    index = _target_index(window, rng)
    offset_km = MIN_TELEPORT_KILOMETRES + severity * (MAX_TELEPORT_KILOMETRES - MIN_TELEPORT_KILOMETRES)
    angle = rng.uniform(0, 2 * np.pi)
    positions[index] += (np.cos(angle) * offset_km * DEGREES_PER_KILOMETRE, np.sin(angle) * offset_km * DEGREES_PER_KILOMETRE)
    labels = [False] * len(positions)
    labels[index] = True
    return _copy_window(window, window.features.copy(), positions), labels


def inject_gradual_drift(window: FeatureWindow, severity: float, seed: int) -> tuple[FeatureWindow, list[bool]]:
    """Accumulate a positional bias so no single report has to be an obvious jump."""
    _validate_severity(severity)
    rng = np.random.default_rng(seed)
    positions = window.positions.copy()
    start = _target_index(window, rng)
    magnitude = (1.0 + 19.0 * severity) * DEGREES_PER_KILOMETRE
    direction = rng.uniform(0, 2 * np.pi)
    offset = np.array((np.cos(direction), np.sin(direction))) * magnitude
    steps = len(positions) - start
    for index in range(start, len(positions)):
        positions[index] += offset * ((index - start + 1) / steps)
    return _copy_window(window, window.features.copy(), positions), [index >= start for index in range(len(positions))]


def inject_freeze_replay(window: FeatureWindow, severity: float, seed: int) -> tuple[FeatureWindow, list[bool]]:
    """Replay a past report despite ongoing movement, producing a frozen/looping track."""
    _validate_severity(severity)
    rng = np.random.default_rng(seed)
    positions = window.positions.copy()
    start = _target_index(window, rng)
    replay_length = max(1, int(round(1 + severity * (len(positions) - start - 1))))
    source_start = max(0, start - replay_length)
    for offset in range(replay_length):
        target = start + offset
        if target >= len(positions):
            break
        positions[target] = positions[source_start + offset]
    labels = [start <= index < start + replay_length for index in range(len(positions))]
    return _copy_window(window, window.features.copy(), positions), labels


def inject_impossible_kinematics(window: FeatureWindow, severity: float, seed: int) -> tuple[FeatureWindow, list[bool]]:
    """Create turns incompatible with a ship's reported straight-line motion."""
    _validate_severity(severity)
    rng = np.random.default_rng(seed)
    features = window.features.copy()
    index = _target_index(window, rng)
    features[index, COG_INDEX] = (features[index, COG_INDEX] + 90 + 90 * severity) % 360
    features[index, RATE_OF_TURN_INDEX] = 127.0 * (0.5 + 0.5 * severity)
    labels = [False] * len(features)
    labels[index] = True
    return _copy_window(window, features, window.positions.copy()), labels


INJECTORS = {
    "teleport_jump": inject_teleport_jump,
    "gradual_drift": inject_gradual_drift,
    "freeze_replay": inject_freeze_replay,
    "impossible_kinematics": inject_impossible_kinematics,
}


def build_synthetic_dataset(
    windows: Sequence[FeatureWindow], patterns: Sequence[str] = SPOOF_PATTERNS,
    severities: Sequence[float] = (0.25, 0.5, 0.75), seed: int = 0,
) -> list[InjectedWindow]:
    """Mix clean controls and seeded spoof patterns into a reproducible evaluation set."""
    if not patterns or not severities:
        raise ValueError("patterns and severities must both be non-empty")
    unknown_patterns = set(patterns) - INJECTORS.keys()
    if unknown_patterns:
        raise ValueError(f"unknown injection patterns: {sorted(unknown_patterns)}")
    for severity in severities:
        _validate_severity(severity)

    rng = np.random.default_rng(seed)
    result: list[InjectedWindow] = []
    for window in windows:
        if rng.random() < CONTROL_FRACTION:
            result.append(InjectedWindow(window, tuple(False for _ in window.positions), None))
            continue
        pattern = str(rng.choice(patterns))
        severity = float(rng.choice(severities))
        injected, labels = INJECTORS[pattern](window, severity, int(rng.integers(0, 2**32 - 1)))
        result.append(InjectedWindow(injected, tuple(labels), pattern))
    return result
