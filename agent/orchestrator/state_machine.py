"""Auditable bounded investigation state machine, deliberately not an LLM classifier."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any

from features.summary import NORMAL_LABEL
from models.bilstm.threshold import OPERATING_THRESHOLD
from tools.freeze_corroboration import corroborate_freeze_replay
from tools.stationary import window_is_stationary

REPORT_CONFIDENCE_THRESHOLD = 0.7  # Uncalibrated Stage 1 placeholder; calibrate with reviewed incidents in Stage 3.

# Per ImplementationPlans/old/Sem5_BigPass_LiveScoring_And_Laya.md section 3: a flag where
# only one detector fired is weaker evidence than one where two or more independently
# agree - this is a first pass at that distinction (a hard cap below
# REPORT_CONFIDENCE_THRESHOLD so it escalates instead of auto-reporting), not a claim
# that it fully solves the followup run's 19.4% control false-positive rate. A proper
# fix (per-vessel-class or per-region threshold normalization) remains future work.
SINGLE_DETECTOR_CONFIDENCE_CAP = 0.5

# BigPass plan section 6: the Laya pattern classifier (tools/pattern_classifier.py) is a
# fourth evidence source. Its vote only counts above this confidence, and it can only do two
# things: agree (lifting the single-detector cap, exactly like a second detector would) or
# contradict with a confident "normal_track" (applying the same cap). It never chooses the
# hypothesis and never forces "benign": it has not been evaluated on this project's data yet,
# and Laya's own docs list negation and label-wording weaknesses. Uncalibrated placeholder,
# to be revisited once the checkpoint has a holdout evaluation (ml/evaluation/laya_export.py).
PATTERN_MIN_CONFIDENCE = 0.7

# Which Laya labels support which hypothesis. The classifier only "agrees" when its label is
# the one the hypothesis implies, so a confident label for a different pattern neither lifts
# the single-detector cap nor counts against the flag. equipment_fault has no matching Laya
# label (the classes are all spoofing patterns), so nothing can agree with it.
HYPOTHESIS_IMPLIED_PATTERNS: dict[str, frozenset[str]] = {
    "freeze_replay": frozenset({"freeze_replay"}),
    "targeted_spoof": frozenset({"teleport_jump", "gradual_drift", "impossible_kinematics"}),
    "equipment_fault": frozenset(),
}

BENIGN_CONFIDENCE = 0.75

# A lone prediction_error flag counts as weak only when its score is at most this multiple of
# OPERATING_THRESHOLD ("slightly above threshold"). Uncalibrated placeholder; revisit once
# reviewed incidents show where benign flags actually sit (scoring/review_stats.py).
BENIGN_MAX_SCORE_MULTIPLE = 2.0

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
    # The scored window's first and last report times, written to incidents.window_start and
    # incidents.window_end. None for callers that do not track a window.
    window_start: Any = None
    window_end: Any = None

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
        """INSERT INTO incidents (mmsi, flagged_at, flagged_position, anomaly_score, anomaly_type, hypothesis, confidence, status, evidence, tool_call_log, report_text, window_start, window_end)
           VALUES ($1,$2,ST_SetSRID(ST_MakePoint($4,$3),4326)::geography,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12,$13,$14)""",
        row["mmsi"], row["flagged_at"], latitude, longitude, row["anomaly_score"], row["anomaly_type"],
        row["hypothesis"], row["confidence"], row["status"], json.dumps(row["evidence"], default=str),
        json.dumps(row["tool_call_log"], default=str), row.get("report_text"),
        row.get("window_start"), row.get("window_end"),
    )

def _pattern_confident_label(evidence: dict[str, Any]) -> str | None:
    """The Laya label, only when the tool ran and is at least PATTERN_MIN_CONFIDENCE sure."""
    result = evidence.get("pattern_classifier") or {}
    confidence = result.get("confidence")
    if not result.get("available") or confidence is None or confidence < PATTERN_MIN_CONFIDENCE:
        return None
    return result.get("pattern")

def _pattern_vote(evidence: dict[str, Any], hypothesis: str) -> str | None:
    """"agrees" / "contradicts" / None (unavailable, low confidence, or a label that says nothing about this hypothesis)."""
    label = _pattern_confident_label(evidence)
    if label is None:
        return None
    if label == NORMAL_LABEL:
        return "contradicts"
    return "agrees" if label in HYPOTHESIS_IMPLIED_PATTERNS.get(hypothesis, frozenset()) else None

def _apply_single_detector_cap(
    anomaly: FlaggedAnomaly, hypothesis: str, confidence: float,
    evidence: dict[str, Any] | None = None, ignore_contradiction: bool = False,
) -> tuple[str, float]:
    """Down-weight confidence when exactly one detector produced this flag.

    Only acts when `detector_votes` is explicitly populated with exactly one entry -
    see that field's own docstring on FlaggedAnomaly for why an empty/untracked value
    must never be treated the same way. Never touches "jamming" (a zone match is
    already a strong, independent signal in its own right, not one of the three score
    detectors this is meant to corroborate against), nor "benign"/"unresolved" (neither
    is a report-confidence tier this cap is meant to gate).

    The Laya pattern classifier (see PATTERN_MIN_CONFIDENCE) acts as one more vote: a
    confident label that matches the hypothesis (HYPOTHESIS_IMPLIED_PATTERNS) lifts the cap, a confident "normal_track" applies it even
    when several detectors voted. `ignore_contradiction` is for the sequence-corroborated
    freeze/replay tier, whose evidence is deterministic and read straight off the track.
    """
    if hypothesis in ("jamming", "benign", "unresolved"):
        return hypothesis, confidence
    vote = _pattern_vote(evidence or {}, hypothesis)
    if vote == "contradicts" and not ignore_contradiction:
        return hypothesis, min(confidence, SINGLE_DETECTOR_CONFIDENCE_CAP)
    if len(anomaly.detector_votes) == 1 and vote != "agrees":
        return hypothesis, min(confidence, SINGLE_DETECTOR_CONFIDENCE_CAP)
    return hypothesis, confidence

def _is_weak_isolated_flag(anomaly: FlaggedAnomaly, evidence: dict[str, Any]) -> bool:
    """A lone, barely-over-threshold prediction_error flag on a stationary window with no corroboration.

    Nothing else backs it: no other detector, no freeze corroboration, and no confident Laya
    label for a spoofing pattern. Such flags resolve to benign instead of escalating.
    """
    if anomaly.detector_votes != frozenset({"prediction_error"}):
        return False
    if anomaly.anomaly_score > OPERATING_THRESHOLD * BENIGN_MAX_SCORE_MULTIPLE:
        return False
    label = _pattern_confident_label(evidence)
    if label is not None and label != NORMAL_LABEL:
        return False
    return window_is_stationary(evidence.get("track_history") or {})

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
    # A score below the model's threshold only means "benign" when no other detector
    # disagrees: a freeze/replay or speed_jump flag (scoring/live_scorer.py) can fire on a
    # window whose prediction error is low, which is exactly the case
    # prediction_error alone was found to miss. Empty detector_votes (untracked/legacy
    # callers) and prediction_error-only votes keep the original behavior.
    other_detector_votes = anomaly.detector_votes - {"prediction_error"}
    if anomaly.anomaly_score < OPERATING_THRESHOLD and not other_detector_votes: return "benign", BENIGN_CONFIDENCE
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
        return _apply_single_detector_cap(anomaly, "freeze_replay", 0.8, evidence, ignore_contradiction=True)
    if _is_weak_isolated_flag(anomaly, evidence):
        return "benign", BENIGN_CONFIDENCE
    # Only this vessel's own history argues against a one-off targeted event. The same
    # pattern on other vessels (same_pattern_elsewhere) is context, not a reason to downgrade.
    if not evidence["incident_history"].get("same_vessel"):
        return _apply_single_detector_cap(anomaly, "targeted_spoof", 0.72, evidence)
    return _apply_single_detector_cap(anomaly, "equipment_fault", 0.55, evidence)

async def investigate(anomaly: FlaggedAnomaly, tools: dict[str, Tool], persist: Persist, report: Report | None = None) -> InvestigationResult:
    """Gather all evidence before branching, then persist either report or escalation."""
    log: list[dict[str, Any]] = []; evidence: dict[str, Any] = {}
    for name in ("track_history", "jamming_zones", "incident_history"):
        result = await tools[name](anomaly); evidence[name] = result; log.append({"tool": name, "result": result})
    # Optional fourth source (tools/pattern_classifier.py). Isolated from the three above:
    # any failure becomes an "unavailable" result so a missing or broken Laya model can never
    # stop an investigation, and callers that pass no such tool behave exactly as before.
    if "pattern_classifier" in tools:
        try:
            result = await tools["pattern_classifier"](anomaly)
        except Exception as error:  # noqa: BLE001
            result = {"available": False, "pattern": None, "confidence": None, "probabilities": None, "reason": f"tool error: {type(error).__name__}"}
        evidence["pattern_classifier"] = result; log.append({"tool": "pattern_classifier", "result": result})
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
    row = {"mmsi": anomaly.mmsi, "flagged_at": anomaly.flagged_at, "anomaly_score": anomaly.anomaly_score, "anomaly_type": anomaly.anomaly_type, "hypothesis": hypothesis, "confidence": confidence, "status": "reported" if state is InvestigationState.REPORTING else "escalated", "evidence": evidence, "tool_call_log": log, "flagged_position": (anomaly.latitude, anomaly.longitude), "window_start": anomaly.window_start, "window_end": anomaly.window_end}
    # A benign call needs no drafted narrative (and no model call spent on it).
    if state is InvestigationState.REPORTING and report is not None and hypothesis != "benign":
        report_text = await report(row)
        # An optional provider may return an empty string after a handled outage or
        # rate limit; keep the database value NULL rather than recording a fake report.
        if report_text:
            row["report_text"] = report_text
    await persist(row)
    return InvestigationResult(InvestigationState.DONE, hypothesis, confidence, evidence, log)
