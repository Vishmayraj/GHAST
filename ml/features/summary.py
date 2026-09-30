"""Text summary of an AIS track window, and the Laya question asked about it.

Two callers must agree on this text exactly, or the fine-tuned classifier is trained on
one thing and served another:

* `evaluation/laya_export.py` builds the fine-tuning set from injected windows,
* `agent/tools/pattern_classifier.py` builds the live input from `track_history` rows.

So the summary only uses columns both sides have (`received_at`, `latitude`, `longitude`,
`sog_knots`, `cog_deg`), and recomputes implied speed from positions and timestamps. It does
NOT read the cached implied-speed feature column: the injectors move positions without
refreshing that column, so it would be stale on exactly the windows we train on.

Pure Python plus features.extract helpers. No torch, no asyncpg, no Laya import.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .extract import (
    COG_INDEX, FREEZE_DISPLACEMENT_EPSILON_KNOTS, MISSING_VALUE, SOG_INDEX,
    haversine_kilometres, implied_speed_knots,
)

# The injector's `None` pattern (a clean control window) is called "normal_track" here:
# Laya's docs warn that choice keys are rendered verbatim, so the label should read like
# what it means rather than an internal name.
NORMAL_LABEL = "normal_track"
PATTERN_LABELS = (NORMAL_LABEL, "teleport_jump", "gradual_drift", "freeze_replay", "impossible_kinematics")
QUESTION_ID = "pattern"

_CRITERIA = {
    NORMAL_LABEL: "ordinary vessel motion: reported speed and course match how the position actually changes",
    "teleport_jump": "one report jumps a long way from the previous one, implying an impossible speed",
    "gradual_drift": "positions slowly slide away from the reported speed and course, with no single big jump",
    "freeze_replay": "earlier positions are repeated exactly, or position stays put while reported speed says the vessel is moving",
    "impossible_kinematics": "reported course or turn is inconsistent with the direction the vessel actually travelled",
}


def pattern_questions() -> dict[str, Any]:
    """The one Laya question set used for both training rows and live prediction."""
    return {QUESTION_ID: {
        "type": "choice",
        "instructions": "Which spoofing pattern best explains this AIS track summary, or is it a normal track?",
        "criteria": dict(_CRITERIA),
    }}


def label_for_pattern(pattern: str | None) -> str:
    """Map the injector's pattern (None for control) to a Laya choice key."""
    if pattern is None:
        return NORMAL_LABEL
    if pattern not in PATTERN_LABELS:
        raise ValueError(f"unknown pattern: {pattern!r}")
    return pattern


def window_to_rows(window: Any) -> list[dict[str, Any]]:
    """Turn a FeatureWindow (duck-typed, so this module never imports pipeline/asyncpg)
    into the `track_history` row shape the live tool sees."""
    rows: list[dict[str, Any]] = []
    for index, timestamp in enumerate(window.timestamps):
        sog = float(window.features[index, SOG_INDEX])
        cog = float(window.features[index, COG_INDEX])
        rows.append({
            "received_at": timestamp,
            "latitude": float(window.positions[index][0]),
            "longitude": float(window.positions[index][1]),
            "sog_knots": None if sog == MISSING_VALUE else sog,
            "cog_deg": None if cog == MISSING_VALUE else cog,
        })
    return rows


def _as_datetime(value: datetime | str) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _bearing_degrees(previous: Mapping[str, Any], current: Mapping[str, Any]) -> float:
    lat_a, lat_b = math.radians(float(previous["latitude"])), math.radians(float(current["latitude"]))
    delta_lon = math.radians(float(current["longitude"]) - float(previous["longitude"]))
    y = math.sin(delta_lon) * math.cos(lat_b)
    x = math.cos(lat_a) * math.sin(lat_b) - math.sin(lat_a) * math.cos(lat_b) * math.cos(delta_lon)
    return math.degrees(math.atan2(y, x)) % 360.0


def _angle_difference(a: float, b: float) -> float:
    """Smallest absolute difference between two compass angles, 0..180."""
    difference = abs(a - b) % 360.0
    return 360.0 - difference if difference > 180.0 else difference


def _revisited_positions(ordered: Sequence[Mapping[str, Any]]) -> int:
    """Reports whose exact coordinates (to ~1 m) match an earlier, non-adjacent report while
    differing from the report just before them: a replayed stretch, not a vessel at rest.

    features.inject.inject_freeze_replay copies earlier positions forward, so this is the
    signature the injected freeze_replay class actually leaves (the position does not sit
    still; it loops back over ground already covered).
    """
    seen: dict[tuple[float, float], int] = {}
    count = 0
    previous_key: tuple[float, float] | None = None
    for index, row in enumerate(ordered):
        key = (round(float(row["latitude"]), 5), round(float(row["longitude"]), 5))
        first_seen = seen.get(key)
        if first_seen is not None and first_seen < index - 1 and key != previous_key:
            count += 1
        seen.setdefault(key, index)
        previous_key = key
    return count


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize_rows(rows: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic multi-line `key: value` description of one chronological track."""
    ordered = sorted(rows, key=lambda row: _as_datetime(row["received_at"]))
    if len(ordered) < 2:
        return "reports: %d\nnot enough reports to describe motion" % len(ordered)

    span_minutes = (_as_datetime(ordered[-1]["received_at"]) - _as_datetime(ordered[0]["received_at"])).total_seconds() / 60.0
    reported = [float(r["sog_knots"]) for r in ordered if r.get("sog_knots") is not None]

    implied: list[float] = []            # implied speed per step (only valid steps)
    implied_minus_reported: list[float] = []
    step_km: list[float] = []
    cog_changes: list[float] = []
    cog_vs_bearing: list[float] = []
    stationary_claims = 0
    longest_stationary_run = run = 0
    for previous, current in zip(ordered, ordered[1:]):
        speed = implied_speed_knots(previous, current)
        if speed == MISSING_VALUE:
            run = 0
            continue
        implied.append(speed)
        step_km.append(haversine_kilometres(
            float(previous["latitude"]), float(previous["longitude"]),
            float(current["latitude"]), float(current["longitude"]),
        ))
        sog = current.get("sog_knots")
        if sog is not None:
            implied_minus_reported.append(speed - float(sog))
            if speed <= FREEZE_DISPLACEMENT_EPSILON_KNOTS and float(sog) > FREEZE_DISPLACEMENT_EPSILON_KNOTS:
                stationary_claims += 1
                run += 1
                longest_stationary_run = max(longest_stationary_run, run)
            else:
                run = 0
        else:
            run = 0
        if previous.get("cog_deg") is not None and current.get("cog_deg") is not None:
            cog_changes.append(_angle_difference(float(previous["cog_deg"]), float(current["cog_deg"])))
        # Only compare course to travel direction when the vessel really moved; at rest the
        # bearing between two jittering fixes is noise.
        if current.get("cog_deg") is not None and speed > 1.0:
            cog_vs_bearing.append(_angle_difference(float(current["cog_deg"]), _bearing_degrees(previous, current)))

    third = max(1, len(implied_minus_reported) // 3)
    trend = _mean(implied_minus_reported[-third:]) - _mean(implied_minus_reported[:third]) if implied_minus_reported else 0.0
    max_reported = max(reported) if reported else 0.0

    lines = [
        f"reports: {len(ordered)}, span_minutes: {span_minutes:.1f}",
        f"reported_speed_knots: mean {_mean(reported):.1f}, max {max_reported:.1f}",
        f"implied_speed_knots: mean {_mean(implied):.1f}, max {max(implied, default=0.0):.1f}",
        f"max_implied_over_max_reported: {max(implied, default=0.0) / max(max_reported, 1.0):.1f}",
        f"max_step_km: {max(step_km, default=0.0):.2f}",
        f"steps_claiming_speed_but_not_moving: {stationary_claims}, longest_run: {longest_stationary_run}",
        f"reports_repeating_earlier_positions: {_revisited_positions(ordered)}",
        f"max_course_change_between_reports_deg: {max(cog_changes, default=0.0):.0f}",
        f"max_course_vs_travel_direction_deg: {max(cog_vs_bearing, default=0.0):.0f}",
        f"implied_minus_reported_speed_trend_knots: {trend:+.1f}",
    ]
    return "\n".join(lines)
