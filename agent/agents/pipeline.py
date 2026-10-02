"""What happens after an incident is stored.

    incident stored (tier A or B)
        |-- fleet-context agent  one ship or many?          -> evidence.fleet_context
        |-- challenger agent     the benign case            -> incidents.challenge
        v   (run in parallel, each isolated)
    triage agent                 queue order                -> incidents.priority
        v
    tier B only: draft the report, verified against the record (report_generator.on_demand)

The investigation (`orchestrator.state_machine.investigate`) is unchanged and deterministic.
These agents add context and ordering around it, and none can change the stored hypothesis or
close an incident. Each step is isolated: a failing agent is logged and skipped, and the
incident is already safely stored before any of them run. Every run is audited in agent_runs.

`db` must be an asyncpg Pool (the scorer passes one): the fleet and challenger agents query at the
same time, and a single asyncpg Connection refuses concurrent operations.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agents.challenger import build_challenger_agent
from agents.context import IncidentContext
from agents.fleet_context import build_fleet_context_agent
from agents.triage import build_triage_agent
from report_generator.on_demand import generate_report
from runtime.agent import Agent, record_run

logger = logging.getLogger("ghast.agent.pipeline")

LOAD_QUERY = """
SELECT id::text AS id, mmsi, flagged_at, ST_Y(flagged_position::geometry) AS latitude,
       ST_X(flagged_position::geometry) AS longitude, anomaly_score, tier, hypothesis, confidence, evidence
FROM incidents WHERE id = $1::uuid
"""
STORE_FLEET_QUERY = """
UPDATE incidents SET evidence = evidence || jsonb_build_object('fleet_context', $2::jsonb), updated_at = now()
WHERE id = $1::uuid
"""
STORE_CHALLENGE_QUERY = "UPDATE incidents SET challenge = $2::jsonb, updated_at = now() WHERE id = $1::uuid"
STORE_PRIORITY_QUERY = "UPDATE incidents SET priority = $2, updated_at = now() WHERE id = $1::uuid"

ThresholdBSource = Callable[[], Awaitable[float | None]]


class IncidentPipeline:
    def __init__(self, db: Any, client: Any, model: str | None, threshold_b: ThresholdBSource | None = None) -> None:
        self._db, self._client, self._model, self._threshold_b = db, client, model, threshold_b
        self._llm_missing_logged = False

    async def _context(self, anomaly: Any, result: Any) -> IncidentContext | None:
        row = await self._db.fetchrow(LOAD_QUERY, result.incident_id)
        if row is None:
            return None
        evidence = row["evidence"]
        evidence = json.loads(evidence) if isinstance(evidence, (str, bytes)) else (evidence or {})
        return IncidentContext(
            incident_id=row["id"], mmsi=row["mmsi"], flagged_at=row["flagged_at"],
            latitude=float(row["latitude"]), longitude=float(row["longitude"]), score=float(row["anomaly_score"]),
            tier=row["tier"], hypothesis=row["hypothesis"], confidence=float(row["confidence"]),
            votes=tuple(sorted(anomaly.detector_votes)), evidence=evidence,
            threshold_a=anomaly.threshold_a, threshold_b=await self._threshold_b() if self._threshold_b else None,
        )

    async def _run(self, agent: Agent, task: dict[str, Any], incident_id: str) -> dict[str, Any] | None:
        try:
            run = await agent.run(task)
            await record_run(self._db, run, incident_id)
            return run.decision
        except Exception:  # noqa: BLE001 - one broken agent must not stop the others
            logger.exception("%s failed for incident %s", agent.name, incident_id)
            return None

    async def __call__(self, anomaly: Any, result: Any) -> None:
        if not getattr(result, "incident_id", None):
            return
        ctx = await self._context(anomaly, result)
        if ctx is None:
            return
        fleet, challenge = await asyncio.gather(
            self._run(build_fleet_context_agent(self._db, ctx, self._client, self._model), {"card": ctx.card()}, ctx.incident_id),
            self._run(build_challenger_agent(self._db, ctx, self._client, self._model), {"card": ctx.card()}, ctx.incident_id),
        )
        if fleet is not None:
            await self._db.execute(STORE_FLEET_QUERY, ctx.incident_id, json.dumps(fleet))
        if challenge is not None:
            await self._db.execute(STORE_CHALLENGE_QUERY, ctx.incident_id, json.dumps(challenge))
        triage = await self._run(
            build_triage_agent(self._db, ctx, self._client, self._model),
            {"card": ctx.card(), "fleet_context": fleet, "challenge": challenge}, ctx.incident_id,
        )
        if triage is not None:
            await self._db.execute(STORE_PRIORITY_QUERY, ctx.incident_id, triage["priority"])
        if ctx.tier == "B":
            await self._auto_report(ctx)

    async def _auto_report(self, ctx: IncidentContext) -> None:
        if self._client is None:
            if not self._llm_missing_logged:
                logger.warning("llm missing: tier B reports are not drafted automatically; use `review.py report <id>` once GROQ_API_KEY is set")
                self._llm_missing_logged = True
            return
        outcome = await generate_report(
            self._db, ctx.incident_id, self._client, auto=True, verifier_client=self._client, verifier_model=self._model,
        )
        logger.info("tier B report for %s: %s", ctx.incident_id, outcome.outcome)
