import pytest
from datetime import datetime, timezone
from orchestrator.state_machine import REPORT_CONFIDENCE_THRESHOLD, FlaggedAnomaly, InvestigationState, form_hypothesis, investigate
from models.bilstm.threshold import OPERATING_THRESHOLD

# OPERATING_THRESHOLD stays None until evaluation/score_checkpoint.py has produced a
# real, held-out-scale threshold (see threshold.py's own provenance comment and
# ImplementationPlans/Sem5_Evaluation_Followup.md section 9) - that hasn't happened yet,
# so these threshold-dependent tests are meaningless (and would hard-fail on the
# `assert ... is not None` alone) until then. Skip, don't fail, in the interim.
requires_operating_threshold = pytest.mark.skipif(
    OPERATING_THRESHOLD is None,
    reason="OPERATING_THRESHOLD is still None pending a real evaluation run; see threshold.py",
)

async def _tool(value):
    async def call(_): return value
    return call

@pytest.mark.asyncio
async def test_reporting_is_audited_and_persisted() -> None:
    saved = []
    async def persist(row): saved.append(row)
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), 0.9, "position", 10, 20)
    tools = {"track_history": await _tool({"positions": []}), "jamming_zones": await _tool({"matched": True}), "incident_history": await _tool({"similar_incidents": []})}
    result = await investigate(anomaly, tools, persist, report=lambda row: _report())
    assert result.state is InvestigationState.DONE
    assert saved[0]["status"] == "reported" and len(saved[0]["tool_call_log"]) == 3

async def _report(): return "draft"

# Pins the exact off-by-a-guessed-constant bug this branch used to have: it
# compared anomaly_score against a hardcoded 0.3 that predated any real
# evaluation and didn't match the model's actual (heavily right-skewed,
# 0.0009-14.4) output scale, so it silently called almost everything benign.
# These two tests fail loudly if that guess ever creeps back in instead of
# OPERATING_THRESHOLD.
_NEUTRAL_EVIDENCE = {"jamming_zones": {"matched": False}, "incident_history": {"similar_incidents": []}}

@requires_operating_threshold
def test_score_just_below_threshold_is_benign() -> None:
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD - 1e-6, "position", 10, 20)
    hypothesis, confidence = form_hypothesis(anomaly, _NEUTRAL_EVIDENCE)
    assert hypothesis == "benign"

@requires_operating_threshold
def test_score_just_above_threshold_is_not_benign() -> None:
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD + 1e-6, "position", 10, 20)
    hypothesis, confidence = form_hypothesis(anomaly, _NEUTRAL_EVIDENCE)
    assert hypothesis != "benign"

# freeze_corroboration tiering: investigate() populates evidence["freeze_corroboration"]
# from tools.freeze_corroboration.corroborate_freeze_replay (see that module); these
# pin form_hypothesis's own handling of it directly, without needing a real
# track_history tool call.
_FREEZE_MATCHED = {"matched": True, "frozen_reports": 2, "total_pairs": 2}
_FREEZE_NOT_MATCHED = {"matched": False, "frozen_reports": 0, "total_pairs": 3}

@requires_operating_threshold
def test_freeze_corroboration_promotes_to_freeze_replay_hypothesis() -> None:
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD + 1e-6, "position", 10, 20)
    hypothesis, confidence = form_hypothesis(anomaly, {**_NEUTRAL_EVIDENCE, "freeze_corroboration": _FREEZE_MATCHED})
    assert hypothesis == "freeze_replay"
    assert confidence >= 0.7  # must clear REPORT_CONFIDENCE_THRESHOLD to auto-report, not escalate

@requires_operating_threshold
def test_freeze_corroboration_not_matched_falls_back_to_existing_tiers() -> None:
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD + 1e-6, "position", 10, 20)
    hypothesis, _ = form_hypothesis(anomaly, {**_NEUTRAL_EVIDENCE, "freeze_corroboration": _FREEZE_NOT_MATCHED})
    assert hypothesis == "targeted_spoof"  # unchanged pre-existing tier, from _NEUTRAL_EVIDENCE's empty similar_incidents

@requires_operating_threshold
def test_missing_freeze_corroboration_key_is_backward_compatible() -> None:
    # An evidence dict built by a caller that predates freeze_corroboration entirely
    # (exactly _NEUTRAL_EVIDENCE's own shape) must fall through identically to the
    # not-matched case above, not raise a KeyError.
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD + 1e-6, "position", 10, 20)
    hypothesis, _ = form_hypothesis(anomaly, _NEUTRAL_EVIDENCE)
    assert hypothesis == "targeted_spoof"

def test_jamming_match_takes_priority_over_freeze_corroboration() -> None:
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), 0.9, "position", 10, 20)
    evidence = {"jamming_zones": {"matched": True}, "incident_history": {"similar_incidents": []}, "freeze_corroboration": _FREEZE_MATCHED}
    hypothesis, confidence = form_hypothesis(anomaly, evidence)
    assert hypothesis == "jamming"
    assert confidence == 0.85

# detector_votes corroboration cap (ImplementationPlans/Sem5_BigPass_LiveScoring_And_Laya.md
# section 3): a flag where only one of the live scorer's detectors fired is weaker
# evidence than one where two or more independently agree.
@requires_operating_threshold
def test_single_detector_vote_caps_confidence_below_report_threshold() -> None:
    anomaly = FlaggedAnomaly(
        1, datetime.now(timezone.utc), OPERATING_THRESHOLD + 1e-6, "position", 10, 20,
        detector_votes=frozenset({"prediction_error"}),
    )
    hypothesis, confidence = form_hypothesis(anomaly, {**_NEUTRAL_EVIDENCE, "freeze_corroboration": _FREEZE_MATCHED})
    assert hypothesis == "freeze_replay"  # the tier itself is unaffected, only its confidence
    assert confidence < REPORT_CONFIDENCE_THRESHOLD  # escalates instead of auto-reporting

@requires_operating_threshold
def test_two_detector_votes_are_not_capped() -> None:
    anomaly = FlaggedAnomaly(
        1, datetime.now(timezone.utc), OPERATING_THRESHOLD + 1e-6, "position", 10, 20,
        detector_votes=frozenset({"prediction_error", "freeze_replay"}),
    )
    hypothesis, confidence = form_hypothesis(anomaly, {**_NEUTRAL_EVIDENCE, "freeze_corroboration": _FREEZE_MATCHED})
    assert hypothesis == "freeze_replay"
    assert confidence == 0.8  # unchanged from the uncapped tier

def test_untracked_detector_votes_is_not_treated_as_single_detector() -> None:
    # The default (no detector_votes passed at all) must behave exactly like every
    # pre-existing test above that never set this field - an empty/untracked value is
    # not the same thing as "exactly one detector fired".
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), 0.9, "position", 10, 20)
    evidence = {"jamming_zones": {"matched": True}, "incident_history": {"similar_incidents": []}, "freeze_corroboration": _FREEZE_MATCHED}
    hypothesis, confidence = form_hypothesis(anomaly, evidence)
    assert hypothesis == "jamming"
    assert confidence == 0.85

@requires_operating_threshold
def test_single_detector_vote_does_not_cap_jamming() -> None:
    # A zone match is its own strong, independent signal - not one of the three score
    # detectors this cap is meant to corroborate against - so it must stay uncapped
    # even when detector_votes has exactly one entry.
    anomaly = FlaggedAnomaly(
        1, datetime.now(timezone.utc), OPERATING_THRESHOLD + 1e-6, "position", 10, 20,
        detector_votes=frozenset({"prediction_error"}),
    )
    evidence = {"jamming_zones": {"matched": True}, "incident_history": {"similar_incidents": []}}
    hypothesis, confidence = form_hypothesis(anomaly, evidence)
    assert hypothesis == "jamming"
    assert confidence == 0.85

@pytest.mark.asyncio
async def test_investigate_logs_detector_corroboration_in_evidence() -> None:
    saved = []
    async def persist(row): saved.append(row)
    anomaly = FlaggedAnomaly(
        1, datetime.now(timezone.utc), 0.9, "position", 10, 20,
        detector_votes=frozenset({"prediction_error", "speed_jump"}),
    )
    tools = {"track_history": await _tool({"positions": []}), "jamming_zones": await _tool({"matched": True}), "incident_history": await _tool({"similar_incidents": []})}
    result = await investigate(anomaly, tools, persist, report=lambda row: _report())
    assert result.evidence["detector_corroboration"] == {"votes": ["prediction_error", "speed_jump"], "count": 2}
    assert saved[0]["evidence"]["detector_corroboration"]["count"] == 2

@pytest.mark.asyncio
async def test_investigate_threads_freeze_corroboration_from_track_history() -> None:
    # Same frozen-position/claimed-speed shape agent/tests/test_freeze_corroboration.py
    # exercises directly against corroborate_freeze_replay - here confirming
    # investigate() actually wires track_history's tool result through to it and into
    # the persisted evidence, not just that form_hypothesis handles the key once given.
    frozen_positions = {
        "positions": [
            {"received_at": datetime(2026, 1, 1, tzinfo=timezone.utc), "latitude": 10.0, "longitude": 20.0, "sog_knots": 12.0, "cog_deg": 90.0},
            {"received_at": datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc), "latitude": 10.0, "longitude": 20.0, "sog_knots": 12.0, "cog_deg": 90.0},
        ]
    }
    saved = []
    async def persist(row): saved.append(row)
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), (OPERATING_THRESHOLD or 0.0) + 1e-6, "position", 10, 20)
    tools = {"track_history": await _tool(frozen_positions), "jamming_zones": await _tool({"matched": False}), "incident_history": await _tool({"similar_incidents": []})}
    result = await investigate(anomaly, tools, persist)
    assert result.evidence["freeze_corroboration"]["matched"] is True
    assert saved[0]["evidence"]["freeze_corroboration"]["matched"] is True
    if OPERATING_THRESHOLD is not None:
        assert result.hypothesis == "freeze_replay"


# A non-model detector firing on a below-threshold window must not be swallowed by the
# benign branch (see form_hypothesis); untracked callers keep the old behavior.
@requires_operating_threshold
def test_other_detector_vote_below_threshold_is_not_benign() -> None:
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD / 10, "freeze_replay", 10, 20, detector_votes=frozenset({"freeze_replay"}))
    hypothesis, _ = form_hypothesis(anomaly, _NEUTRAL_EVIDENCE)
    assert hypothesis != "benign"

@requires_operating_threshold
def test_prediction_error_only_vote_below_threshold_is_still_benign() -> None:
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD / 10, "prediction_error", 10, 20, detector_votes=frozenset({"prediction_error"}))
    hypothesis, _ = form_hypothesis(anomaly, _NEUTRAL_EVIDENCE)
    assert hypothesis == "benign"
