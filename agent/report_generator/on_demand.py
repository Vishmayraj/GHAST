"""Draft an incident report only when an analyst asks for one.

The investigation stores evidence and a hypothesis and calls no LLM. This module is the
one place a report gets written: it loads a stored incident, checks it clears
REPORT_DRAFT_CONFIDENCE_THRESHOLD, drafts through Groq, and stores the text. Callers are
the review CLI (`python review.py report <id>`) now, and the dashboard's "generate report"
button through the backend later.

An incident that already has a report is returned as stored instead of drafted again,
unless `force` is set, so repeated clicks cost nothing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from orchestrator.state_machine import REPORT_DRAFT_CONFIDENCE_THRESHOLD
from report_generator.report import draft_report

logger = logging.getLogger(__name__)

SHOW_QUERY = """
SELECT id, mmsi, flagged_at, anomaly_score, anomaly_type, hypothesis, confidence, status,
       evidence, report_text
FROM incidents
WHERE id = $1::uuid
"""

# Without force the write only lands while report_text is still NULL, so two concurrent
# requests cannot both store a draft; the loser reads the winner's text instead.
STORE_QUERY = """
UPDATE incidents
SET report_text = $2, report_generated_at = now(), updated_at = now()
WHERE id = $1::uuid AND (report_text IS NULL OR $3::boolean)
RETURNING id
"""

Outcome = Literal["generated", "already_drafted", "not_eligible", "not_found", "failed"]


@dataclass(frozen=True)
class ReportResult:
    outcome: Outcome
    report_text: str | None = None
    detail: str | None = None


def is_report_eligible(confidence: float | None) -> bool:
    return confidence is not None and confidence >= REPORT_DRAFT_CONFIDENCE_THRESHOLD


def _decode_evidence(value: Any) -> dict[str, Any]:
    """asyncpg returns jsonb as text unless a codec is registered; accept either."""
    if isinstance(value, (str, bytes)):
        value = json.loads(value)
    return value or {}


async def generate_report(db: Any, incident_id: str, client: Any, force: bool = False) -> ReportResult:
    """Draft and store a report for one incident, or say why not."""
    row = await db.fetchrow(SHOW_QUERY, incident_id)
    if row is None:
        return ReportResult("not_found")
    incident = dict(row)
    if incident.get("report_text") and not force:
        return ReportResult("already_drafted", incident["report_text"])
    if not is_report_eligible(incident.get("confidence")):
        return ReportResult(
            "not_eligible",
            detail=f"confidence {incident.get('confidence')} is below the report threshold {REPORT_DRAFT_CONFIDENCE_THRESHOLD}",
        )
    incident["evidence"] = _decode_evidence(incident.get("evidence"))
    try:
        text = await draft_report(incident, client)
    except Exception as error:  # noqa: BLE001
        # A provider outage or rate limit must not look like a stored report.
        logger.exception("report drafting failed for incident %s", incident_id)
        return ReportResult("failed", detail=f"{type(error).__name__}: {error}")
    if not text:
        return ReportResult("failed", detail="the provider returned no text")
    stored = await db.fetchrow(STORE_QUERY, incident_id, text, force)
    if stored is None:
        latest = await db.fetchrow(SHOW_QUERY, incident_id)
        return ReportResult("already_drafted", dict(latest)["report_text"] if latest else None)
    return ReportResult("generated", text)
