"""Sequence-based freeze/replay corroboration from raw track history.

evaluation.baselines.freeze_replay_detector (ml/) scores one AISObservation at a
time against its own precomputed `implied_speed` feature column
(features.extract.IMPLIED_SPEED_INDEX), which the live scorer derives per-window in
scoring/live_scorer.py::window_observations before the detector ever runs.
The agent has no feature window here, only agent/tools/track_history.py's raw
position rows (`received_at`, `latitude`, `longitude`, `sog_knots`, `cog_deg`) -
exactly the row shape features.extract.implied_speed_knots already expects for
training, so this reconciles the two by reusing that function directly on the raw
sequence instead of re-deriving haversine/elapsed-time math a second time, or
forcing track_history's output through a feature-window shape it was never built to
have.

Deliberately does not import evaluation.baselines: that module (via .datasets ->
features.pipeline) pulls in asyncpg, a DB dependency this bounded, DB-optional agent
test suite (see agent/README.md) has never needed. FREEZE_DISPLACEMENT_EPSILON_KNOTS
lives in features.extract precisely so both call sites can share one number without
either depending on the other's heavier import chain.
"""
from __future__ import annotations

from typing import Any

from features.extract import FREEZE_DISPLACEMENT_EPSILON_KNOTS, MISSING_VALUE, implied_speed_knots

# Corroboration looks at the same 20-report window the detectors scored, not the whole
# track_history span. track_history ends at the flag, so the last N positions are the
# window ending at the flagged report.
FREEZE_WINDOW_REPORTS = 20

# Frozen pairs needed before the window counts as corroborated. One bad SOG at rest
# produces a single frozen pair, so one pair must never be enough.
# Uncalibrated: 3 is a starting value, to be revisited with reviewed incidents
# (scoring/review_stats.py).
MIN_FROZEN_PAIRS = 3


def corroborate_freeze_replay(track_history: dict[str, Any]) -> dict[str, Any]:
    """Independently check track_history's raw position sequence for a freeze/replay pattern.

    Returns {"matched": bool, "frozen_reports": int, "total_pairs": int}. "matched" is
    True when at least MIN_FROZEN_PAIRS consecutive pairs of reports, among the last
    FREEZE_WINDOW_REPORTS positions, claim ongoing movement
    (`sog_knots` above the shared epsilon) while the position itself barely moved
    (`implied_speed_knots` between the two reports is at or below
    FREEZE_DISPLACEMENT_EPSILON_KNOTS) - the same signal
    evaluation.baselines.freeze_replay_detector scores, computed here straight from
    the sequence a human auditor can also read off track_history's own positions,
    rather than a derived feature column.

    Tolerates a missing or empty `track_history` (fewer than two positions, or no
    "positions" key at all) by returning a non-matching, zero-count result rather
    than raising - form_hypothesis (orchestrator/state_machine.py) must be able to
    call this even when track_history's own tool call failed or returned nothing,
    without that failure silently becoming a match.
    """
    positions = (track_history.get("positions") or [])[-FREEZE_WINDOW_REPORTS:]
    frozen_reports = 0
    total_pairs = 0
    for previous, current in zip(positions, positions[1:]):
        sog = current.get("sog_knots")
        if sog is None:
            continue
        implied_speed = implied_speed_knots(previous, current)
        if implied_speed == MISSING_VALUE:
            # Non-positive elapsed time between reports (out-of-order or duplicate
            # timestamps) - not a real reading either way, not a freeze.
            continue
        total_pairs += 1
        if implied_speed <= FREEZE_DISPLACEMENT_EPSILON_KNOTS and sog > FREEZE_DISPLACEMENT_EPSILON_KNOTS:
            frozen_reports += 1
    return {"matched": frozen_reports >= MIN_FROZEN_PAIRS, "frozen_reports": frozen_reports, "total_pairs": total_pairs}
