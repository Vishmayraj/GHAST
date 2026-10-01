"""Stationary-window check on track_history's raw reports.

A vessel at anchor or alongside produces noisy position and SOG reports that prediction
error scores slightly high. That is the main benign source the agent can recognise from
the track alone, so `form_hypothesis` uses this to let such flags resolve to `benign`.
"""
from __future__ import annotations

from statistics import median
from typing import Any

# Same 20-report window the detectors scored; track_history ends at the flag, so the last
# N positions are the window ending at the flagged report.
STATIONARY_WINDOW_REPORTS = 20

# Median reported SOG at or below this (knots) counts as stationary.
# Uncalibrated placeholder, to be revisited with reviewed incidents.
STATIONARY_MEDIAN_SOG_KNOTS = 0.5

# Fewer SOG readings than this and the window is not called stationary, because a
# handful of readings is not enough to claim it.
# Uncalibrated placeholder.
STATIONARY_MIN_SOG_READINGS = 10


def window_is_stationary(track_history: dict[str, Any]) -> bool:
    """True when the last window of reports shows the vessel effectively not moving."""
    positions = (track_history.get("positions") or [])[-STATIONARY_WINDOW_REPORTS:]
    readings = [row["sog_knots"] for row in positions if row.get("sog_knots") is not None]
    if len(readings) < STATIONARY_MIN_SOG_READINGS:
        return False
    return median(readings) <= STATIONARY_MEDIAN_SOG_KNOTS
