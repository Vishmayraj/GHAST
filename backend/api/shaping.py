"""Turn stored incident rows into the JSON the console reads.

Pure functions, no I/O. Stored `evidence` and `tool_call_log` are never returned raw: they hold
other vessels' rows (`incident_history.same_pattern_elsewhere`) and whole track dumps. The
console gets sentences and counts built here instead.
"""
from __future__ import annotations

import json
from typing import Any

# Same value as agent/tools/freeze_corroboration.py::MIN_FROZEN_PAIRS, shown beside the count.
FREEZE_PAIRS_NEEDED = 3

DETECTOR_NAMES = {"prediction_error": "Prediction error", "freeze_replay": "Freeze check", "speed_jump": "Speed jump"}


def decode(value: Any, default: Any) -> Any:
    """asyncpg returns JSONB as text unless a codec is set; accept both."""
    if value is None:
        return default
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


def _count(rows: Any) -> int:
    return len(rows) if isinstance(rows, list) else 0


def votes_from(evidence: dict[str, Any], anomaly_score: float | None) -> list[dict[str, Any]]:
    fired = set((evidence.get("detector_corroboration") or {}).get("votes") or [])
    freeze = evidence.get("freeze_corroboration") or {}
    rows: list[dict[str, Any]] = []
    for key in ("prediction_error", "freeze_replay", "speed_jump"):
        if key == "speed_jump" and key not in fired:
            continue  # only listed when a speed threshold is configured and it fired
        row = {"detector": DETECTOR_NAMES[key], "fired": key in fired, "value": None, "threshold": None,
               "label": None, "confidence": None, "mode": "active"}
        if key == "prediction_error":
            row["value"] = anomaly_score
        if key == "freeze_replay":
            row["fired"] = key in fired or bool(freeze.get("matched"))
            row["value"] = freeze.get("frozen_reports")
            row["threshold"] = FREEZE_PAIRS_NEEDED
        rows.append(row)
    pattern = evidence.get("pattern_classifier") or {}
    if pattern.get("available"):
        label = pattern.get("pattern")
        rows.append({"detector": "Pattern classifier", "fired": label not in (None, "normal_track"), "value": None,
                     "threshold": None, "label": label, "confidence": pattern.get("confidence"), "mode": "active"})
    return rows


def fleet_context_from(evidence: dict[str, Any]) -> dict[str, Any] | None:
    """Counts only. `isolated` is None when the agent said there were too few neighbours to judge."""
    fleet = evidence.get("fleet_context")
    if not isinstance(fleet, dict) or fleet.get("scope") not in ("isolated", "area", "insufficient"):
        return None
    scope = fleet["scope"]
    nearby = int(fleet.get("nearby_incidents") or 0)
    return {
        "scope": scope,
        "cluster_vessels": nearby + 1 if scope == "area" else 1,
        "neighbours_checked": int(fleet.get("neighbours_checked") or 0),
        "radius_km": None,
        "isolated": True if scope == "isolated" else False if scope == "area" else None,
        "note": fleet.get("note"),
    }


def evidence_lines(evidence: dict[str, Any]) -> list[dict[str, str]]:
    lines: list[str] = []
    votes = (evidence.get("detector_corroboration") or {}).get("votes") or []
    if votes:
        lines.append("Detectors that fired: " + ", ".join(DETECTOR_NAMES.get(v, v) for v in votes) + ".")
    freeze = evidence.get("freeze_corroboration") or {}
    if freeze.get("matched"):
        lines.append(f"{freeze.get('frozen_reports')} of {freeze.get('total_pairs')} report pairs repeat a position while reported speed stays above the minimum.")
    zones = evidence.get("jamming_zones") or {}
    if zones.get("matched"):
        name = (zones.get("zone") or {}).get("name") or "an unnamed zone"
        lines.append(f"The position is inside a known jamming area: {name}.")
    else:
        lines.append("No known jamming area contains the position.")
    history = evidence.get("incident_history") or {}
    own = _count(history.get("same_vessel"))
    lines.append("No earlier incident on this vessel." if own == 0 else f"{own} earlier incident{'s' if own != 1 else ''} on this vessel.")
    elsewhere = _count(history.get("same_pattern_elsewhere"))
    if elsewhere:
        lines.append(f"{elsewhere} incident{'s' if elsewhere != 1 else ''} with the same pattern on other vessels.")
    pattern = evidence.get("pattern_classifier") or {}
    if pattern.get("available") and pattern.get("pattern"):
        conf = pattern.get("confidence")
        lines.append(f"The pattern classifier reads this window as {str(pattern['pattern']).replace('_', ' ')}" + (f" ({round(conf * 100)}%)." if conf is not None else "."))
    fleet = fleet_context_from(evidence)
    if fleet and fleet["note"]:
        lines.append(str(fleet["note"]))
    return [{"text": text} for text in lines]


def log_summary(entry: dict[str, Any]) -> dict[str, str]:
    tool = str(entry.get("tool") or "unknown")
    result = entry.get("result") or {}
    if tool == "track_history":
        text = f"Pulled {_count(result.get('positions'))} reports for this vessel up to the flag."
    elif tool == "jamming_zones":
        text = "The position is inside a known jamming area." if result.get("matched") else "Checked the position against known jamming areas. No match."
    elif tool == "incident_history":
        text = f"Found {_count(result.get('same_vessel'))} earlier incidents on this vessel and {_count(result.get('same_pattern_elsewhere'))} with the same pattern elsewhere."
    elif tool == "pattern_classifier":
        text = (f"Pattern classifier: {result.get('pattern')}." if result.get("available") else f"Pattern classifier unavailable: {result.get('reason')}.")
    else:
        text = "Ran."
    return {"tool": tool, "summary": text}


def challenge_from(value: Any) -> dict[str, Any] | None:
    challenge = decode(value, None)
    if not isinstance(challenge, dict) or "benign_likelihood" not in challenge:
        return None
    return {"benign_likelihood": challenge.get("benign_likelihood"), "argument": challenge.get("argument")}


def track_points(rows: list[Any], flagged_at: Any) -> list[dict[str, Any]]:
    return [{"time": r["received_at"], "lat": r["latitude"], "lon": r["longitude"],
             "predicted_lat": None, "predicted_lon": None, "flagged": r["received_at"] == flagged_at} for r in rows]


def review_stats_payload(stats: Any, minimum: int) -> dict[str, Any]:
    return {
        "reviewed": stats.reviewed, "unclear": stats.unclear, "minimum": minimum,
        "rows": [{"hypothesis": g.key, "reviewed": g.reviewed, "unclear": g.unclear, "correct": g.correct, "precision": g.precision}
                 for g in stats.by_hypothesis],
        "by_votes": [{"votes": g.key, "reviewed": g.reviewed, "unclear": g.unclear, "correct": g.correct, "precision": g.precision}
                     for g in stats.by_votes],
    }
