"""Draft an incident report: automatically for tier B, on a button press for tier A.

An incident stores evidence and a hypothesis and calls no LLM while the scorer runs. Reports
come from here:

* tier B (score at or above threshold B): the incident pipeline calls `generate_report(auto=True)`
  right after the incident is stored;
* tier A: an analyst presses the console's "generate report" button (today:
  `python review.py report <id>`), which calls the same function.

Every draft is checked by the report verifier agent against the stored incident. A failing draft
is regenerated once with the verifier's issues as feedback; if it still fails it is stored with a
visible warning at the top, never silently.

Lifecycle. A report lives for REPORT_TTL_HOURS from when it was drafted (`report_expires_at`).
`purge_expired_reports` clears the text after that; `delete_report` clears it when an analyst
deletes it on purpose. Either way the incident, its evidence and its verdict are kept forever
(they are training data); only `report_text` goes, and `report_delete_reason` records which
('expired' or 'analyst'). A report can be generated again afterwards. A repeated request never
drafts twice while a live report exists, unless `force` is set.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from agents.report_verifier import build_report_verifier
from report_generator.report import draft_report
from runtime.agent import record_run

logger = logging.getLogger(__name__)

REPORT_TTL_HOURS = 24
MAX_DRAFT_ATTEMPTS = 2  # one draft plus one corrected redraft

SHOW_QUERY = """
SELECT id, mmsi, flagged_at, anomaly_score, anomaly_type, hypothesis, confidence, status, tier,
       evidence, challenge, report_text, review_verdict
FROM incidents
WHERE id = $1::uuid
"""

# Without force the write only lands while there is no live report, so two concurrent requests
# cannot both store a draft; the loser reads the winner's text instead.
STORE_QUERY = """
UPDATE incidents
SET report_text = $2, report_generated_at = now(), report_expires_at = now() + make_interval(hours => $5::int),
    report_deleted_at = NULL, report_delete_reason = NULL, report_auto = $4,
    report_verification = $6::jsonb, updated_at = now()
WHERE id = $1::uuid AND (report_text IS NULL OR $3::boolean)
RETURNING id
"""

PURGE_QUERY = """
UPDATE incidents
SET report_text = NULL, report_deleted_at = now(), report_delete_reason = 'expired', updated_at = now()
WHERE report_text IS NOT NULL AND report_expires_at IS NOT NULL AND report_expires_at < now()
"""

DELETE_QUERY = """
UPDATE incidents
SET report_text = NULL, report_deleted_at = now(), report_delete_reason = 'analyst', updated_at = now()
WHERE id = $1::uuid AND report_text IS NOT NULL
RETURNING id
"""

Outcome = Literal["generated", "already_drafted", "not_found", "failed"]


@dataclass(frozen=True)
class ReportResult:
    outcome: Outcome
    report_text: str | None = None
    detail: str | None = None
    verified: bool | None = None


def _decode(value: Any) -> Any:
    """asyncpg returns jsonb as text unless a codec is registered; accept either."""
    if isinstance(value, (str, bytes)):
        value = json.loads(value)
    return value


def _facts(incident: dict[str, Any]) -> dict[str, Any]:
    return {key: incident.get(key) for key in ("mmsi", "hypothesis", "confidence", "status", "tier", "anomaly_score", "review_verdict")}


def _unverified_banner(issues: list[str]) -> str:
    listed = "; ".join(issues)[:600]
    return f"> WARNING: this draft did not pass verification against the incident record ({listed}). Treat its claims with care.\n\n"


async def generate_report(
    db: Any, incident_id: str, client: Any, force: bool = False, auto: bool = False,
    verifier_client: Any | None = None, verifier_model: str | None = None,
) -> ReportResult:
    """Draft, verify and store a report for one incident, or say why not.

    `client` drafts the text (Groq). `verifier_client`/`verifier_model` run the verifier agent;
    without them the verifier uses its deterministic checks only.
    """
    row = await db.fetchrow(SHOW_QUERY, incident_id)
    if row is None:
        return ReportResult("not_found")
    incident = dict(row)
    if incident.get("report_text") and not force:
        return ReportResult("already_drafted", incident["report_text"])
    incident["evidence"] = _decode(incident.get("evidence")) or {}
    incident["challenge"] = _decode(incident.get("challenge"))
    facts = _facts(incident)

    feedback: list[str] | None = None
    text, verdict = "", {"verdict": "fail", "issues": ["no draft produced"]}
    for attempt in range(1, MAX_DRAFT_ATTEMPTS + 1):
        try:
            text = await draft_report(incident, client, feedback)
        except Exception as error:  # noqa: BLE001
            # A provider outage or rate limit must not look like a stored report.
            logger.exception("report drafting failed for incident %s", incident_id)
            return ReportResult("failed", detail=f"{type(error).__name__}: {error}")
        if not text:
            return ReportResult("failed", detail="the provider returned no text")
        run = await build_report_verifier(facts, text, verifier_client, verifier_model).run({"incident_id": incident_id, "attempt": attempt})
        await record_run(db, run, incident_id)
        verdict = run.decision
        if verdict["verdict"] == "pass":
            break
        feedback = verdict["issues"]
    verified = verdict["verdict"] == "pass"
    stored_text = text if verified else _unverified_banner(verdict["issues"]) + text
    verification = {"verdict": verdict["verdict"], "issues": verdict["issues"], "attempts": attempt}
    stored = await db.fetchrow(STORE_QUERY, incident_id, stored_text, force, auto, REPORT_TTL_HOURS, json.dumps(verification))
    if stored is None:
        latest = await db.fetchrow(SHOW_QUERY, incident_id)
        return ReportResult("already_drafted", dict(latest)["report_text"] if latest else None)
    return ReportResult("generated", stored_text, verified=verified)


async def purge_expired_reports(db: Any) -> int:
    """Clear report text past its 24 hour life. Incidents and evidence are untouched."""
    status = await db.execute(PURGE_QUERY)
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0


async def delete_report(db: Any, incident_id: str) -> bool:
    """An analyst deleting a report on purpose (the console's delete button)."""
    return await db.fetchrow(DELETE_QUERY, incident_id) is not None
