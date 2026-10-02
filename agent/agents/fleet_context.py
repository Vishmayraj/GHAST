"""Fleet-context agent: one ship, or many?

The HLD's central distinction (docs/hld-vs-current.md): one vessel off while its neighbours are
fine points at spoofing aimed at it; many vessels off in the same area points at jamming. There
is no clustering code yet (plan 03), so this agent answers the question from what exists: who
else was reporting near the flagged position around the flagged time, and whether other
vessels have incidents there. It reads, decides, and writes only its own decision.
"""
from __future__ import annotations

from typing import Any

from agents.context import IncidentContext
from runtime.agent import Agent, ToolSpec

# Uncalibrated. A flag is only "isolated" if enough neighbours were reporting to compare against.
DEFAULT_RADIUS_KM = 25.0
DEFAULT_WINDOW_MINUTES = 60
MIN_NEIGHBOURS_TO_JUDGE = 3
MIN_NEARBY_INCIDENTS_FOR_AREA = 2
MAX_RADIUS_KM = 100.0

NEIGHBOURS_QUERY = """
SELECT DISTINCT ON (mmsi) mmsi, latitude, longitude, received_at,
       ST_Distance(position, ST_SetSRID(ST_MakePoint($3, $2), 4326)::geography) / 1000.0 AS km
FROM vessel_position
WHERE received_at BETWEEN $1::timestamptz - make_interval(mins => $5::int)
                      AND $1::timestamptz + make_interval(mins => $5::int)
  AND mmsi <> $6
  AND ST_DWithin(position, ST_SetSRID(ST_MakePoint($3, $2), 4326)::geography, $4 * 1000.0)
  AND message_type IS DISTINCT FROM 'historical'
ORDER BY mmsi, received_at DESC
LIMIT 60
"""

NEARBY_INCIDENTS_QUERY = """
SELECT id::text, mmsi, hypothesis, tier, anomaly_score, flagged_at,
       ST_Distance(flagged_position, ST_SetSRID(ST_MakePoint($3, $2), 4326)::geography) / 1000.0 AS km
FROM incidents
WHERE mmsi <> $6 AND flagged_position IS NOT NULL
  AND flagged_at BETWEEN $1::timestamptz - make_interval(mins => $5::int * 6)
                     AND $1::timestamptz + make_interval(mins => $5::int * 6)
  AND ST_DWithin(flagged_position, ST_SetSRID(ST_MakePoint($3, $2), 4326)::geography, $4 * 1000.0)
ORDER BY flagged_at DESC
LIMIT 20
"""

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "scope": {"type": "string", "enum": ["isolated", "area", "insufficient"]},
        "neighbours_checked": {"type": "integer"},
        "nearby_incidents": {"type": "integer"},
        "note": {"type": "string", "description": "One or two sentences citing the counts."},
    },
    "required": ["scope", "neighbours_checked", "nearby_incidents", "note"],
}

SYSTEM_PROMPT = """You are GHAST's fleet-context agent. A vessel's position report was flagged as anomalous.
Question: is this ONE vessel (isolated: suggests spoofing aimed at it, or its own equipment) or an AREA effect (many vessels: suggests jamming)?
Use vessels_nearby (who was reporting near the flag) and incidents_nearby (other vessels' incidents there). Widen the radius once if there are too few neighbours.
Say "insufficient" when fewer than 3 neighbours were reporting: absence of neighbours is not evidence of isolation. Report only counts the tools gave you. Call finish once."""


def validate(decision: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
    scope = decision.get("scope")
    if scope not in ("isolated", "area", "insufficient"):
        raise ValueError("scope must be isolated, area or insufficient")
    neighbours = observations.get("vessels_nearby", {}).get("count")
    incidents = observations.get("incidents_nearby", {}).get("count")
    if neighbours is None or incidents is None:
        raise ValueError("call vessels_nearby and incidents_nearby before deciding")
    if scope == "isolated" and neighbours < MIN_NEIGHBOURS_TO_JUDGE:
        raise ValueError(f"cannot call a vessel isolated with only {neighbours} neighbours; use insufficient")
    if scope == "area" and incidents < MIN_NEARBY_INCIDENTS_FOR_AREA:
        raise ValueError(f"area needs at least {MIN_NEARBY_INCIDENTS_FOR_AREA} nearby incidents on other vessels, saw {incidents}")
    note = str(decision.get("note") or "").strip()
    if not note:
        raise ValueError("a note is required")
    # Counts are taken from the tools, not from what the model typed.
    return {"scope": scope, "neighbours_checked": neighbours, "nearby_incidents": incidents, "note": note[:400]}


def baseline(task: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
    neighbours = int((observations.get("vessels_nearby") or {}).get("count", 0))
    incidents = int((observations.get("incidents_nearby") or {}).get("count", 0))
    if incidents >= MIN_NEARBY_INCIDENTS_FOR_AREA:
        scope = "area"
    elif neighbours >= MIN_NEIGHBOURS_TO_JUDGE:
        scope = "isolated"
    else:
        scope = "insufficient"
    return {"scope": scope, "neighbours_checked": neighbours, "nearby_incidents": incidents,
            "note": f"baseline: {neighbours} neighbours reporting, {incidents} incidents on other vessels nearby"}


def build_fleet_context_agent(db: Any, ctx: IncidentContext, client: Any, model: str | None) -> Agent:
    async def vessels_nearby(args: dict) -> dict:
        radius = min(float(args.get("radius_km", DEFAULT_RADIUS_KM)), MAX_RADIUS_KM)
        rows = await db.fetch(NEIGHBOURS_QUERY, ctx.flagged_at, ctx.latitude, ctx.longitude, radius, DEFAULT_WINDOW_MINUTES, ctx.mmsi)
        return {"radius_km": radius, "window_minutes": DEFAULT_WINDOW_MINUTES, "count": len(rows),
                "nearest_km": [round(float(r["km"]), 1) for r in sorted(rows, key=lambda r: r["km"])[:5]]}

    async def incidents_nearby(args: dict) -> dict:
        radius = min(float(args.get("radius_km", DEFAULT_RADIUS_KM)), MAX_RADIUS_KM)
        rows = await db.fetch(NEARBY_INCIDENTS_QUERY, ctx.flagged_at, ctx.latitude, ctx.longitude, radius, DEFAULT_WINDOW_MINUTES, ctx.mmsi)
        return {"radius_km": radius, "count": len(rows),
                "hypotheses": sorted({r["hypothesis"] for r in rows})}

    radius_schema = {"type": "object", "properties": {"radius_km": {"type": "number", "description": f"default {DEFAULT_RADIUS_KM}, max {MAX_RADIUS_KM}"}}}
    return Agent(
        "fleet_context_agent", SYSTEM_PROMPT,
        [ToolSpec("vessels_nearby", "Other vessels that reported within a radius of the flag, around the flag time.", radius_schema, vessels_nearby),
         ToolSpec("incidents_nearby", "Incidents on other vessels within a radius of the flag, in the hours around it.", radius_schema, incidents_nearby)],
        DECISION_SCHEMA, validate, baseline, client=client, model=model,
        fallback_tools=("vessels_nearby", "incidents_nearby"),
    )
