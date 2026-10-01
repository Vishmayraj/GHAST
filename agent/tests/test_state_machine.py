import pytest
from datetime import datetime, timedelta, timezone
from orchestrator.state_machine import (
    BENIGN_CONFIDENCE, BENIGN_MAX_SCORE_MULTIPLE, REPORT_CONFIDENCE_THRESHOLD, FlaggedAnomaly, InvestigationState,
    form_hypothesis, investigate, persist_incident,
)
from models.bilstm.threshold import OPERATING_THRESHOLD
from tools.freeze_corroboration import MIN_FROZEN_PAIRS

# OPERATING_THRESHOLD stays None until evaluation/score_checkpoint.py has produced a
# real, held-out-scale threshold (see threshold.py's own provenance comment and
# ImplementationPlans/old/Sem5_Evaluation_Followup.md section 9) - that hasn't happened yet,
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
    tools = {"track_history": await _tool({"positions": []}), "jamming_zones": await _tool({"matched": True}), "incident_history": await _tool({"same_vessel": [], "same_pattern_elsewhere": []})}
    result = await investigate(anomaly, tools, persist)
    assert result.state is InvestigationState.DONE
    assert saved[0]["status"] == "reported" and len(saved[0]["tool_call_log"]) == 3


# Pins the exact off-by-a-guessed-constant bug this branch used to have: it
# compared anomaly_score against a hardcoded 0.3 that predated any real
# evaluation and didn't match the model's actual (heavily right-skewed,
# 0.0009-14.4) output scale, so it silently called almost everything benign.
# These two tests fail loudly if that guess ever creeps back in instead of
# OPERATING_THRESHOLD.
_NEUTRAL_EVIDENCE = {"jamming_zones": {"matched": False}, "incident_history": {"same_vessel": [], "same_pattern_elsewhere": []}}

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
_FREEZE_MATCHED = {"matched": True, "frozen_reports": 3, "total_pairs": 3}
_FREEZE_NOT_MATCHED = {"matched": False, "frozen_reports": 0, "total_pairs": 3}

@requires_operating_threshold
def test_freeze_corroboration_promotes_to_freeze_replay_hypothesis() -> None:
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD + 1e-6, "position", 10, 20)
    hypothesis, confidence = form_hypothesis(anomaly, {**_NEUTRAL_EVIDENCE, "freeze_corroboration": _FREEZE_MATCHED})
    assert hypothesis == "freeze_replay"
    assert confidence >= 0.7  # must clear REPORT_CONFIDENCE_THRESHOLD to be reported, not escalated

@requires_operating_threshold
def test_freeze_corroboration_not_matched_falls_back_to_existing_tiers() -> None:
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD + 1e-6, "position", 10, 20)
    hypothesis, _ = form_hypothesis(anomaly, {**_NEUTRAL_EVIDENCE, "freeze_corroboration": _FREEZE_NOT_MATCHED})
    assert hypothesis == "targeted_spoof"  # unchanged pre-existing tier, from _NEUTRAL_EVIDENCE's empty same_vessel history

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
    evidence = {"jamming_zones": {"matched": True}, "incident_history": {"same_vessel": [], "same_pattern_elsewhere": []}, "freeze_corroboration": _FREEZE_MATCHED}
    hypothesis, confidence = form_hypothesis(anomaly, evidence)
    assert hypothesis == "jamming"
    assert confidence == 0.85

# detector_votes corroboration cap (ImplementationPlans/old/Sem5_BigPass_LiveScoring_And_Laya.md
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
    assert confidence < REPORT_CONFIDENCE_THRESHOLD  # escalates instead of being reported

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
    evidence = {"jamming_zones": {"matched": True}, "incident_history": {"same_vessel": [], "same_pattern_elsewhere": []}, "freeze_corroboration": _FREEZE_MATCHED}
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
    evidence = {"jamming_zones": {"matched": True}, "incident_history": {"same_vessel": [], "same_pattern_elsewhere": []}}
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
    tools = {"track_history": await _tool({"positions": []}), "jamming_zones": await _tool({"matched": True}), "incident_history": await _tool({"same_vessel": [], "same_pattern_elsewhere": []})}
    result = await investigate(anomaly, tools, persist)
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
            {"received_at": datetime(2026, 1, 1, 0, minute, tzinfo=timezone.utc), "latitude": 10.0, "longitude": 20.0, "sog_knots": 12.0, "cog_deg": 90.0}
            for minute in range(MIN_FROZEN_PAIRS + 1)
        ]
    }
    saved = []
    async def persist(row): saved.append(row)
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), (OPERATING_THRESHOLD or 0.0) + 1e-6, "position", 10, 20)
    tools = {"track_history": await _tool(frozen_positions), "jamming_zones": await _tool({"matched": False}), "incident_history": await _tool({"same_vessel": [], "same_pattern_elsewhere": []})}
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


# benign must be reachable from a real flag: a lone, barely-over-threshold prediction_error
# vote on a stationary window with nothing corroborating it.
def _stationary_track(sog: float = 0.1, count: int = 20) -> dict:
    return {"positions": [
        {"received_at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i), "latitude": 10.0, "longitude": 20.0, "sog_knots": sog, "cog_deg": 0.0}
        for i in range(count)
    ]}

def _weak_flag(score_multiple: float = 1.5, votes=frozenset({"prediction_error"})) -> FlaggedAnomaly:
    return FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD * score_multiple, "prediction_error", 10, 20, detector_votes=votes)

def _weak_evidence(track: dict, **extra) -> dict:
    return {**_NEUTRAL_EVIDENCE, "track_history": track, "freeze_corroboration": {"matched": False}, **extra}

@requires_operating_threshold
def test_lone_weak_flag_on_a_stationary_window_is_benign() -> None:
    hypothesis, confidence = form_hypothesis(_weak_flag(), _weak_evidence(_stationary_track()))
    assert hypothesis == "benign"
    assert confidence == BENIGN_CONFIDENCE

@requires_operating_threshold
def test_weak_flag_on_a_moving_vessel_is_not_benign() -> None:
    hypothesis, _ = form_hypothesis(_weak_flag(), _weak_evidence(_stationary_track(sog=12.0)))
    assert hypothesis != "benign"

@requires_operating_threshold
def test_strong_score_on_a_stationary_window_is_not_benign() -> None:
    hypothesis, _ = form_hypothesis(_weak_flag(score_multiple=BENIGN_MAX_SCORE_MULTIPLE + 1), _weak_evidence(_stationary_track()))
    assert hypothesis != "benign"

@requires_operating_threshold
def test_a_second_detector_vote_blocks_benign() -> None:
    flag = _weak_flag(votes=frozenset({"prediction_error", "speed_jump"}))
    hypothesis, _ = form_hypothesis(flag, _weak_evidence(_stationary_track()))
    assert hypothesis != "benign"

@requires_operating_threshold
def test_untracked_detector_votes_never_resolve_to_benign_by_the_weak_flag_rule() -> None:
    hypothesis, _ = form_hypothesis(_weak_flag(votes=frozenset()), _weak_evidence(_stationary_track()))
    assert hypothesis != "benign"

@requires_operating_threshold
def test_a_confident_spoofing_label_from_laya_blocks_benign() -> None:
    laya = {"pattern_classifier": {"available": True, "pattern": "gradual_drift", "confidence": 0.95}}
    hypothesis, _ = form_hypothesis(_weak_flag(), _weak_evidence(_stationary_track(), **laya))
    assert hypothesis != "benign"

@requires_operating_threshold
def test_a_confident_normal_track_label_does_not_block_benign() -> None:
    laya = {"pattern_classifier": {"available": True, "pattern": "normal_track", "confidence": 0.95}}
    hypothesis, _ = form_hypothesis(_weak_flag(), _weak_evidence(_stationary_track(), **laya))
    assert hypothesis == "benign"

@requires_operating_threshold
def test_too_few_sog_readings_is_not_called_stationary() -> None:
    hypothesis, _ = form_hypothesis(_weak_flag(), _weak_evidence(_stationary_track(count=3)))
    assert hypothesis != "benign"

@requires_operating_threshold
def test_freeze_corroboration_outranks_the_benign_rule() -> None:
    evidence = _weak_evidence(_stationary_track(), freeze_corroboration=_FREEZE_MATCHED)
    hypothesis, _ = form_hypothesis(_weak_flag(), evidence)
    assert hypothesis == "freeze_replay"

@pytest.mark.asyncio
@requires_operating_threshold
async def test_benign_incident_is_persisted_with_no_report_text() -> None:
    saved = []
    async def persist(row): saved.append(row)
    tools = {"track_history": await _tool(_stationary_track()), "jamming_zones": await _tool({"matched": False}),
             "incident_history": await _tool({"same_vessel": [], "same_pattern_elsewhere": []})}
    await investigate(_weak_flag(), tools, persist)
    assert saved[0]["hypothesis"] == "benign"
    assert "report_text" not in saved[0]

# incidents.window_start / window_end come from the scored window carried on the flag.
@pytest.mark.asyncio
async def test_investigate_carries_the_scored_window_into_the_row() -> None:
    start, end = datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, 0, 19, tzinfo=timezone.utc)
    saved = []
    async def persist(row): saved.append(row)
    anomaly = FlaggedAnomaly(1, end, 0.9, "position", 10, 20, window_start=start, window_end=end)
    tools = {"track_history": await _tool({"positions": []}), "jamming_zones": await _tool({"matched": True}),
             "incident_history": await _tool({"same_vessel": [], "same_pattern_elsewhere": []})}
    await investigate(anomaly, tools, persist)
    assert (saved[0]["window_start"], saved[0]["window_end"]) == (start, end)

class _FakeInsertConnection:
    def __init__(self) -> None:
        self.calls = []
    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        return "00000000-0000-0000-0000-000000000001"

@pytest.mark.asyncio
async def test_persist_incident_writes_window_columns() -> None:
    start, end = datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, 0, 19, tzinfo=timezone.utc)
    row = {"mmsi": 1, "flagged_at": end, "flagged_position": (10.0, 20.0), "anomaly_score": 0.9, "anomaly_type": "position",
           "hypothesis": "jamming", "confidence": 0.85, "status": "reported", "evidence": {}, "tool_call_log": [],
           "window_start": start, "window_end": end}
    connection = _FakeInsertConnection()
    assert await persist_incident(connection, row) == "00000000-0000-0000-0000-000000000001"
    query, args = connection.calls[0]
    assert "window_start, window_end, tier" in query and "$13,$14,$15" in query
    assert args[-3:] == (start, end, "A")


def test_the_report_draft_threshold_sits_above_the_reported_line() -> None:
    from orchestrator.state_machine import REPORT_DRAFT_CONFIDENCE_THRESHOLD
    assert REPORT_DRAFT_CONFIDENCE_THRESHOLD > REPORT_CONFIDENCE_THRESHOLD


@pytest.mark.asyncio
async def test_a_reported_incident_is_never_given_report_text_during_investigation() -> None:
    saved = []
    async def persist(row): saved.append(row)
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), 0.9, "position", 10, 20)
    tools = {"track_history": await _tool({"positions": []}), "jamming_zones": await _tool({"matched": True}),
             "incident_history": await _tool({"same_vessel": [], "same_pattern_elsewhere": []})}
    await investigate(anomaly, tools, persist)
    assert saved[0]["status"] == "reported" and "report_text" not in saved[0]


@pytest.mark.asyncio
async def test_persist_incident_writes_the_tier() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    row = {"mmsi": 1, "flagged_at": now, "flagged_position": (10.0, 20.0), "anomaly_score": 0.9, "anomaly_type": "position",
           "hypothesis": "jamming", "confidence": 0.85, "status": "reported", "evidence": {}, "tool_call_log": [], "tier": "B"}
    connection = _FakeInsertConnection()
    await persist_incident(connection, row)
    assert connection.calls[0][1][-1] == "B"


@pytest.mark.asyncio
async def test_investigate_returns_the_stored_incident_id_and_carries_the_tier() -> None:
    saved = []
    async def persist(row):
        saved.append(row)
        return "abc"
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), 0.9, "position", 10, 20, tier="B")
    tools = {"track_history": await _tool({"positions": []}), "jamming_zones": await _tool({"matched": True}),
             "incident_history": await _tool({"same_vessel": [], "same_pattern_elsewhere": []})}
    result = await investigate(anomaly, tools, persist)
    assert result.incident_id == "abc" and saved[0]["tier"] == "B"


def test_benign_tests_are_relative_to_the_active_threshold_a_not_the_legacy_constant() -> None:
    quiet = {"jamming_zones": {}, "incident_history": {"same_vessel": [], "same_pattern_elsewhere": []}}
    below = FlaggedAnomaly(1, datetime.now(timezone.utc), 0.5, "prediction_error", 10, 20, threshold_a=2.0)
    above = FlaggedAnomaly(1, datetime.now(timezone.utc), 2.5, "prediction_error", 10, 20, threshold_a=2.0,
                           detector_votes=frozenset({"prediction_error", "freeze_replay"}))
    assert form_hypothesis(below, quiet)[0] == "benign"
    assert form_hypothesis(above, quiet)[0] != "benign"
