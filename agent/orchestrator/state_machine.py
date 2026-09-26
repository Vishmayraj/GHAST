"""Auditable bounded investigation state machine, deliberately not an LLM classifier."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
import json
from typing import Any

REPORT_CONFIDENCE_THRESHOLD = 0.7  # Uncalibrated Stage 1 placeholder; calibrate with reviewed incidents in Stage 3.

class InvestigationState(str, Enum):
    RECEIVED = "received"; GATHERING_EVIDENCE = "gathering_evidence"; HYPOTHESIZING = "hypothesizing"; REPORTING = "reporting"; ESCALATING = "escalating"; DONE = "done"

@dataclass(frozen=True)
class FlaggedAnomaly:
    mmsi: int; flagged_at: Any; anomaly_score: float; anomaly_type: str; latitude: float; longitude: float

@dataclass(frozen=True)
class InvestigationResult:
    state: InvestigationState; hypothesis: str; confidence: float; evidence: dict[str, Any]; tool_call_log: list[dict[str, Any]]

Tool = Callable[[FlaggedAnomaly], Awaitable[dict[str, Any]]]
Persist = Callable[[dict[str, Any]], Awaitable[None]]
Report = Callable[[dict[str, Any]], Awaitable[str]]

async def persist_incident(connection: Any, row: dict[str, Any]) -> None:
    """Store the complete audit trail in the schema-owned incidents record."""
    latitude, longitude = row["flagged_position"]
    await connection.execute(
        """INSERT INTO incidents (mmsi, flagged_at, flagged_position, anomaly_score, anomaly_type, hypothesis, confidence, status, evidence, tool_call_log, report_text)
           VALUES ($1,$2,ST_SetSRID(ST_MakePoint($4,$3),4326)::geography,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12)""",
        row["mmsi"], row["flagged_at"], latitude, longitude, row["anomaly_score"], row["anomaly_type"],
        row["hypothesis"], row["confidence"], row["status"], json.dumps(row["evidence"], default=str),
        json.dumps(row["tool_call_log"], default=str), row.get("report_text"),
    )

def form_hypothesis(anomaly: FlaggedAnomaly, evidence: dict[str, Any]) -> tuple[str, float]:
    """Use transparent Stage 1 rules until reviewed incidents support learned decisions."""
    if evidence["jamming_zones"].get("matched"): return "jamming", 0.85
    if anomaly.anomaly_score < 0.3: return "benign", 0.75
    if not evidence["incident_history"].get("similar_incidents"): return "targeted_spoof", 0.72
    return "equipment_fault", 0.55

async def investigate(anomaly: FlaggedAnomaly, tools: dict[str, Tool], persist: Persist, report: Report | None = None) -> InvestigationResult:
    """Gather all evidence before branching, then persist either report or escalation."""
    log: list[dict[str, Any]] = []; evidence: dict[str, Any] = {}
    for name in ("track_history", "jamming_zones", "incident_history"):
        result = await tools[name](anomaly); evidence[name] = result; log.append({"tool": name, "result": result})
    hypothesis, confidence = form_hypothesis(anomaly, evidence)
    state = InvestigationState.REPORTING if confidence >= REPORT_CONFIDENCE_THRESHOLD else InvestigationState.ESCALATING
    row = {"mmsi": anomaly.mmsi, "flagged_at": anomaly.flagged_at, "anomaly_score": anomaly.anomaly_score, "anomaly_type": anomaly.anomaly_type, "hypothesis": hypothesis, "confidence": confidence, "status": "reported" if state is InvestigationState.REPORTING else "escalated", "evidence": evidence, "tool_call_log": log, "flagged_position": (anomaly.latitude, anomaly.longitude)}
    if state is InvestigationState.REPORTING and report is not None: row["report_text"] = await report(row)
    await persist(row)
    return InvestigationResult(InvestigationState.DONE, hypothesis, confidence, evidence, log)
