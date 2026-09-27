"""Auditable bounded investigation state machine, deliberately not an LLM classifier."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any

from models.bilstm.threshold import OPERATING_THRESHOLD
from tools.freeze_corroboration import corroborate_freeze_replay

REPORT_CONFIDENCE_THRESHOLD = 0.7  # Uncalibrated Stage 1 placeholder; calibrate with reviewed incidents in Stage 3.

# Per ImplementationPlans/Sem5_BigPass_LiveScoring_And_Laya.md section 3: a flag where
# only one detector fired is weaker evidence than one where two or more independently
# agree - this is a first pass at that distinction (a hard cap below
# REPORT_CONFIDENCE_THRESHOLD so it escalates instead of auto-reporting), not a claim
# that it fully solves the followup run's 19.4% control false-positive rate. A proper
# fix (per-vessel-class or per-region threshold normalization) remains future work.
SINGLE_DETECTOR_CONFIDENCE_CAP = 0.5

class InvestigationState(str, Enum):
    RECEIVED = "received"; GATHERING_EVIDENCE = "gathering_evidence"; HYPOTHESIZING = "hypothesizing"; REPORTING = "reporting"; ESCALATING = "escalating"; DONE = "done"

@dataclass(frozen=True)
class FlaggedAnomaly:
    mmsi: int; flagged_at: Any; anomaly_score: float; anomaly_type: str; latitude: float; longitude: float
    # Which of scoring/live_scorer.py's own detectors (prediction_error, freeze_replay,
    # speed_jump - see ml/evaluation/baselines.py) independently flagged this window,
    # e.g. frozenset({"prediction_error"}) vs frozenset({"prediction_error",
    # "freeze_replay"}). Defaults to empty, meaning "not tracked by this caller" - every
    # caller that predates this field (every test in agent/tests/, any other future
    # caller that only has a single score) gets today's unchanged behavior.
    # form_hypothesis only ever down-weights confidence when this is explicitly
    # populated with exactly one entry; it never infers "single-detector" from an
    # empty/untracked value, so it can't silently start over-escalating callers that
    # never populate this field at all.
    detector_votes: frozenset[str] = field(default_factory=frozenset)

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

def _apply_single_detector_cap(anomaly: FlaggedAnomaly, hypothesis: str, confidence: float) -> tuple[str, float]:
    """Down-weight confidence when exactly one detector produced this flag.

    Only acts when `detector_votes` is explicitly populated with exactly one entry -
    see that field's own docstring on FlaggedAnomaly for why an empty/untracked value
    must never be treated the same way. Never touches "jamming" (a zone match is
    already a strong, independent signal in its own right, not one of the three score
    detectors this is meant to corroborate against), nor "benign"/"unresolved" (neither
    is a report-confidence tier this cap is meant to gate).
    """
    if len(anomaly.detector_votes) == 1 and hypothesis not in ("jamming", "benign", "unresolved"):
        return hypothesis, min(confidence, SINGLE_DETECTOR_CONFIDENCE_CAP)
    return hypothesis, confidence

def form_hypothesis(anomaly: FlaggedAnomaly, evidence: dict[str, Any]) -> tuple[str, float]:
    """Use transparent Stage 1 rules until reviewed incidents support learned decisions."""
    if evidence["jamming_zones"].get("matched"): return "jamming", 0.85
    if OPERATING_THRESHOLD is None:
        # Should not happen once ml/models/bilstm/threshold.py is finalized (see that
        # module's provenance comment); a guessed fallback constant here would silently
        # misclassify at whatever scale a future retrained model happens to score at.
        # "unresolved" is the schema's own default hypothesis value (backend/models/schema.sql)
        # and a confidence below REPORT_CONFIDENCE_THRESHOLD routes this straight to
        # ESCALATING for human review rather than auto-reporting a guess.
        return "unresolved", 0.0
    if anomaly.anomaly_score < OPERATING_THRESHOLD: return "benign", 0.75
    # investigate() populates evidence["freeze_corroboration"] (see
    # tools.freeze_corroboration.corroborate_freeze_replay) from track_history's raw
    # position sequence - an independent signal from the model's own anomaly_score.
    # A caller that built its own evidence dict without that key (older callers, or
    # the tests in test_state_machine.py exercising form_hypothesis directly) gets
    # `{}` here, which .get("matched") reads as not-corroborated, falling straight
    # through to the pre-existing targeted_spoof/equipment_fault tiers unchanged -
    # this branch only ever adds a new, more specific tier, never removes the old ones.
    if evidence.get("freeze_corroboration", {}).get("matched"):
        # Two independent signals agreeing (the model's own threshold crossing, plus
        # a frozen/replayed position visible directly in track_history) earns a
        # higher, auto-reporting confidence than the single-signal tiers below,
        # though still short of jamming's stronger direct zone match above.
        return _apply_single_detector_cap(anomaly, "freeze_replay", 0.8)
    if not evidence["incident_history"].get("similar_incidents"):
        return _apply_single_detector_cap(anomaly, "targeted_spoof", 0.72)
    return _apply_single_detector_cap(anomaly, "equipment_fault", 0.55)

async def investigate(anomaly: FlaggedAnomaly, tools: dict[str, Tool], persist: Persist, report: Report | None = None) -> InvestigationResult:
    """Gather all evidence before branching, then persist either report or escalation."""
    log: list[dict[str, Any]] = []; evidence: dict[str, Any] = {}
    for name in ("track_history", "jamming_zones", "incident_history"):
        result = await tools[name](anomaly); evidence[name] = result; log.append({"tool": name, "result": result})
    # Derived, not a tool call: computed once here from track_history's own result
    # so it's logged in evidence (persisted, and available to the report drafter)
    # without inflating tool_call_log's count of actual evidence-gathering calls.
    evidence["freeze_corroboration"] = corroborate_freeze_replay(evidence["track_history"])
    # Audit trail for the corroboration section 3 of the BigPass plan asks for: which of
    # the live scorer's own detectors independently voted on this window, not just the
    # single anomaly_score. Logged even when empty (an untracked/legacy caller) so a
    # reviewer reading a persisted incident can always tell which case they're looking at.
    evidence["detector_corroboration"] = {"votes": sorted(anomaly.detector_votes), "count": len(anomaly.detector_votes)}
    hypothesis, confidence = form_hypothesis(anomaly, evidence)
    state = InvestigationState.REPORTING if confidence >= REPORT_CONFIDENCE_THRESHOLD else InvestigationState.ESCALATING
    row = {"mmsi": anomaly.mmsi, "flagged_at": anomaly.flagged_at, "anomaly_score": anomaly.anomaly_score, "anomaly_type": anomaly.anomaly_type, "hypothesis": hypothesis, "confidence": confidence, "status": "reported" if state is InvestigationState.REPORTING else "escalated", "evidence": evidence, "tool_call_log": log, "flagged_position": (anomaly.latitude, anomaly.longitude)}
    if state is InvestigationState.REPORTING and report is not None: row["report_text"] = await report(row)
    await persist(row)
    return InvestigationResult(InvestigationState.DONE, hypothesis, confidence, evidence, log)
