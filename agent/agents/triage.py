"""Triage agent: orders the analyst's queue.

Within a tier (B above A) incidents are listed by `priority`, 0 to 1, set here from everything
the other agents found. It decides ORDER only. It never hides, closes or downgrades an
incident, and every incident stays visible to any analyst.
"""
from __future__ import annotations

from typing import Any

from agents.context import IncidentContext
from runtime.agent import Agent, ToolSpec

# Uncalibrated weights for the baseline; revisit with reviewed incidents (scoring/review_stats.py).
W_CONFIDENCE, W_STRENGTH, W_SCOPE, W_NOT_BENIGN = 0.35, 0.25, 0.15, 0.25
# The challenger can move priority by at most this share, so it can never bury an incident.
MAX_CHALLENGER_EFFECT = 0.25

QUEUE_QUERY = """
SELECT count(*) AS open_a FROM incidents
WHERE tier = 'A' AND review_verdict IS NULL AND created_at > now() - interval '24 hours'
"""
IMPORTANCE_QUERY = "SELECT ship_type FROM vessel_static WHERE mmsi = $1"

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "priority": {"type": "number", "description": "0 (look last) to 1 (look first), among incidents of the same tier."},
        "rationale": {"type": "string", "description": "One or two sentences."},
    },
    "required": ["priority", "rationale"],
}

SYSTEM_PROMPT = """You are GHAST's triage agent. You set the order in which analysts look at new incidents. You set a priority from 0 to 1; you cannot hide or close anything.
Consider: how confident the investigation is, how far the score is over its threshold, whether the pattern is isolated to one vessel or an area effect, the challenger's benign argument (weigh it, but it can move priority by at most a quarter), the queue pressure, and the vessel type.
Use incident_card and queue_pressure first. Be consistent: similar evidence should get similar priority. Call finish once."""


def _strength(card: dict[str, Any]) -> float:
    a, b, score = card.get("threshold_a"), card.get("threshold_b"), card.get("score") or 0.0
    if a and b and b > a:
        return max(0.0, min(1.0, (score - a) / (b - a)))
    return 0.5


def validate(decision: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
    try:
        priority = float(decision["priority"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("priority must be a number") from None
    if not 0.0 <= priority <= 1.0:
        raise ValueError("priority must be between 0 and 1")
    rationale = str(decision.get("rationale") or "").strip()
    if not rationale:
        raise ValueError("a rationale is required")
    if "incident_card" not in observations:
        raise ValueError("read incident_card before deciding")
    return {"priority": round(priority, 3), "rationale": rationale[:400]}


def baseline(task: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
    card = observations.get("incident_card") or task.get("card") or {}
    fleet = task.get("fleet_context") or {}
    challenge = task.get("challenge") or {}
    scope = {"area": 1.0, "isolated": 0.7}.get(fleet.get("scope"), 0.5)
    benign = float(challenge.get("benign_likelihood", 0.0))
    base = W_CONFIDENCE * float(card.get("confidence") or 0.0) + W_STRENGTH * _strength(card) + W_SCOPE * scope
    without_challenger = base + W_NOT_BENIGN
    with_challenger = base + W_NOT_BENIGN * (1.0 - benign)
    # The challenger can lower priority by at most MAX_CHALLENGER_EFFECT of what it would be without it.
    priority = max(with_challenger, without_challenger * (1.0 - MAX_CHALLENGER_EFFECT))
    return {"priority": round(min(1.0, priority), 3),
            "rationale": f"baseline: confidence {card.get('confidence')}, strength {_strength(card):.2f}, scope {fleet.get('scope', 'unknown')}, benign argument {benign:.2f}"}


def build_triage_agent(db: Any, ctx: IncidentContext, client: Any, model: str | None) -> Agent:
    async def incident_card(_: dict) -> dict:
        return {**ctx.card(), "strength_between_a_and_b": _strength(ctx.card())}

    async def queue_pressure(_: dict) -> dict:
        row = await db.fetchrow(QUEUE_QUERY)
        return {"unreviewed_tier_a_last_24h": int(row["open_a"]) if row else 0}

    async def vessel_type(_: dict) -> dict:
        row = await db.fetchrow(IMPORTANCE_QUERY, ctx.mmsi)
        return {"ship_type": row["ship_type"] if row else None}

    none = {"type": "object", "properties": {}}
    return Agent(
        "triage_agent", SYSTEM_PROMPT,
        [ToolSpec("incident_card", "The incident's scores, tier, hypothesis, votes, Laya vote and thresholds.", none, incident_card),
         ToolSpec("queue_pressure", "How many tier A incidents are waiting for review.", none, queue_pressure),
         ToolSpec("vessel_type", "AIS ship type code of the vessel, if known.", none, vessel_type)],
        DECISION_SCHEMA, validate, baseline, client=client, model=model, fallback_tools=("incident_card", "queue_pressure"),
    )
