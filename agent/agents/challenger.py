"""Challenger agent: argues the benign case.

Every other part of the investigation looks for reasons the flag is real. This agent's only
job is the opposite: find the best honest argument that nothing is wrong (a vessel at anchor
with noisy reports, a weak score barely over threshold, a vessel an analyst already cleared
before), and say how strong it is. Its output lowers an incident's review priority and is shown
to the analyst. It cannot change the hypothesis or close an incident: a wrong "benign" would
hide a real spoof, so a human always makes that call.
"""
from __future__ import annotations

from statistics import median
from typing import Any

from agents.context import IncidentContext
from runtime.agent import Agent, ToolSpec
from tools.stationary import window_is_stationary

# The challenger may not hide an incident: its influence on priority is capped (see triage).
MIN_TOOLS_CONSULTED = 2

PROFILE_QUERY = "SELECT ship_type, destination FROM vessel_static WHERE mmsi = $1"
OWN_HISTORY_QUERY = """
SELECT hypothesis, tier, review_verdict, flagged_at
FROM incidents WHERE mmsi = $1 AND id <> $2::uuid ORDER BY flagged_at DESC LIMIT 10
"""

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "benign_likelihood": {"type": "number", "description": "0 (no benign argument) to 1 (almost certainly benign)."},
        "argument": {"type": "string", "description": "The single strongest benign explanation, in one or two sentences, citing tool results."},
        "cited": {"type": "array", "items": {"type": "string"}, "description": "Names of the tools your argument rests on."},
    },
    "required": ["benign_likelihood", "argument", "cited"],
}

SYSTEM_PROMPT = """You are GHAST's challenger. A position report was flagged as a possible spoofing event and the rest of the system is looking for reasons it is real.
Your job is the opposite: build the strongest honest argument that this flag is benign (anchored or moored vessel with noisy reports, score barely over threshold, vessel analysts cleared before, a legitimate reason for the pattern).
Use your tools: track_motion, vessel_profile, own_history, score_context. Consult at least two. Only use facts the tools returned.
Be calibrated: if the evidence for a benign explanation is weak, give a low benign_likelihood. Do not argue for benign just because that is your role. Call finish once."""


def validate(decision: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
    try:
        likelihood = float(decision["benign_likelihood"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("benign_likelihood must be a number") from None
    if not 0.0 <= likelihood <= 1.0:
        raise ValueError("benign_likelihood must be between 0 and 1")
    argument = str(decision.get("argument") or "").strip()
    if not argument:
        raise ValueError("an argument is required")
    consulted = [name for name in observations if not str(name).startswith("_")]
    if len(consulted) < MIN_TOOLS_CONSULTED:
        raise ValueError(f"consult at least {MIN_TOOLS_CONSULTED} tools before deciding")
    cited = [c for c in (decision.get("cited") or []) if c in observations]
    return {"benign_likelihood": round(likelihood, 3), "argument": argument[:500], "cited": cited}


def baseline(task: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
    """A transparent heuristic: each independent benign sign adds weight, capped below certainty."""
    signs, weight = [], 0.0
    motion = observations.get("track_motion") or {}
    context = observations.get("score_context") or {}
    history = observations.get("own_history") or {}
    if motion.get("stationary"):
        signs.append("the vessel was effectively stationary"); weight += 0.35
    ratio = context.get("score_over_threshold_a")
    if ratio is not None and ratio < 2.0:
        signs.append(f"score only {ratio:.1f}x threshold A"); weight += 0.25
    if not context.get("corroborated"):
        signs.append("no second detector, freeze check or Laya vote backs it"); weight += 0.15
    if history.get("benign_verdicts", 0) > 0:
        signs.append("an analyst cleared this vessel before"); weight += 0.25
    likelihood = min(weight, 0.9)
    argument = ("baseline: " + "; ".join(signs)) if signs else "baseline: no benign explanation found in the evidence"
    return {"benign_likelihood": round(likelihood, 3), "argument": argument, "cited": [k for k in ("track_motion", "score_context", "own_history") if k in observations]}


def build_challenger_agent(db: Any, ctx: IncidentContext, client: Any, model: str | None) -> Agent:
    async def track_motion(_: dict) -> dict:
        positions = (ctx.evidence.get("track_history") or {}).get("positions") or []
        sog = [p["sog_knots"] for p in positions if p.get("sog_knots") is not None]
        return {"reports": len(positions), "median_sog_knots": round(median(sog), 2) if sog else None,
                "max_sog_knots": max(sog) if sog else None, "stationary": window_is_stationary(ctx.evidence.get("track_history") or {})}

    async def vessel_profile(_: dict) -> dict:
        row = await db.fetchrow(PROFILE_QUERY, ctx.mmsi)
        return {"known": row is not None, "ship_type": row["ship_type"] if row else None,
                "destination": row["destination"] if row else None}

    async def own_history(_: dict) -> dict:
        rows = [dict(r) for r in await db.fetch(OWN_HISTORY_QUERY, ctx.mmsi, ctx.incident_id)]
        return {"prior_incidents": len(rows),
                "benign_verdicts": sum(1 for r in rows if r["review_verdict"] == "benign"),
                "confirmed_spoof_verdicts": sum(1 for r in rows if r["review_verdict"] == "confirmed_spoof"),
                "hypotheses": sorted({r["hypothesis"] for r in rows})}

    async def score_context(_: dict) -> dict:
        card = ctx.card()
        a = ctx.threshold_a
        laya = card["laya"]
        return {"score": ctx.score, "threshold_a": a, "score_over_threshold_a": (ctx.score / a) if a else None,
                "detector_votes": card["detector_votes"], "freeze_corroborated": card["freeze_corroborated"],
                "laya": laya,
                "corroborated": len(ctx.votes) > 1 or card["freeze_corroborated"] or bool(laya["pattern"] and laya["pattern"] != "normal_track")}

    none = {"type": "object", "properties": {}}
    return Agent(
        "challenger_agent", SYSTEM_PROMPT,
        [ToolSpec("track_motion", "Motion summary of the scored window: reports, speeds, stationary or not.", none, track_motion),
         ToolSpec("vessel_profile", "Static data on the vessel: ship type, declared destination.", none, vessel_profile),
         ToolSpec("own_history", "This vessel's earlier incidents and any analyst verdicts on them.", none, own_history),
         ToolSpec("score_context", "How strong the flag is: score vs threshold A, detector votes, Laya, corroboration.", none, score_context)],
        DECISION_SCHEMA, validate, baseline, client=client, model=model,
        fallback_tools=("track_motion", "own_history", "score_context"),
    )
