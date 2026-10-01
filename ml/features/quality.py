"""Decide which report-to-report transitions the BiLSTM can fairly be asked to score.

The model predicts the next lat/lon *delta* in degrees and is never told how much time passed
(`docs/ml-pipeline.md`, "Features"). Its error is therefore the raw size of a position step, and
that step is meaningless in four situations that real traffic produces all the time:

* a coordinate that is not a position (AIS "not available" is lat 91 / lon 181, plus junk),
* a non-positive time step (duplicate or out-of-order report),
* a long silence between two reports (the vessel simply moved while nobody was listening),
* a longitude step across the antimeridian (179.9 -> -179.9 reads as a 359 degree jump).

Scoring those as anomalies is what pushes live p99.9 error to 3.8 degrees (about 420 km) while
the historical p99.9 is 0.12 (`ml/reports/`). This module marks them "unscorable" so they are
counted and reported, never flagged. It is pure (no I/O, no torch) so the live scorer, the
offline evaluator and the tests all share one definition.

Skipping a transition does not hide it: the live scorer counts every skip by reason, and a
gap that is itself suspicious is a different detector's job.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import numpy as np

REASON_INVALID_COORDINATE = "invalid_coordinate"
REASON_NON_POSITIVE_DT = "non_positive_dt"
REASON_LONG_GAP = "long_gap"
REASON_ANTIMERIDIAN = "antimeridian"
REASONS = (REASON_INVALID_COORDINATE, REASON_NON_POSITIVE_DT, REASON_LONG_GAP, REASON_ANTIMERIDIAN)

# Uncalibrated: ten minutes. Class A vessels report every 2 s to 3 min underway, so a gap above
# this is a dropout, not a step the model was trained to predict. Revisit with the dt breakdown
# that `ml/evaluation/diagnostics.py` prints against real traffic.
MAX_SCOREABLE_GAP_SECONDS = 600.0

# A longitude step bigger than this cannot be a real step between two consecutive reports.
ANTIMERIDIAN_STEP_DEGREES = 180.0


def valid_coordinate(latitude: float, longitude: float) -> bool:
    """False for NaN/inf, out-of-range values and the AIS sentinels (91, 181)."""
    if not (np.isfinite(latitude) and np.isfinite(longitude)):
        return False
    return -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0


def transition_reasons(
    positions: np.ndarray,
    timestamps: Sequence[datetime],
    max_gap_seconds: float = MAX_SCOREABLE_GAP_SECONDS,
) -> list[str | None]:
    """One entry per report: None when the step into it is scoreable, else the first reason.

    Index 0 has no step into it and is always None (the scorers never read it).
    `positions` is (n, 2) lat/lon; `timestamps` has the same length.
    """
    if len(positions) != len(timestamps):
        raise ValueError("positions and timestamps must be the same length")
    reasons: list[str | None] = [None] * len(positions)
    for index in range(1, len(positions)):
        previous, current = positions[index - 1], positions[index]
        if not (valid_coordinate(*previous) and valid_coordinate(*current)):
            reasons[index] = REASON_INVALID_COORDINATE
            continue
        elapsed = (timestamps[index] - timestamps[index - 1]).total_seconds()
        if elapsed <= 0:
            reasons[index] = REASON_NON_POSITIVE_DT
        elif elapsed > max_gap_seconds:
            reasons[index] = REASON_LONG_GAP
        elif abs(current[1] - previous[1]) > ANTIMERIDIAN_STEP_DEGREES:
            reasons[index] = REASON_ANTIMERIDIAN
    return reasons


def count_reasons(reasons: Sequence[str | None]) -> dict[str, int]:
    """Counts of each reason that appears, for logs and stats rows."""
    counts: dict[str, int] = {}
    for reason in reasons:
        if reason is not None:
            counts[reason] = counts.get(reason, 0) + 1
    return counts
