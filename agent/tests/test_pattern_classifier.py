"""pattern_classifier tool and its (deliberately narrow) effect on form_hypothesis.

No Laya, torch, or transformers: the predictor is an injected fake, exactly as the tool is
designed to be used in tests. The real `load_laya_predictor` is not exercised here (it needs a
fine-tuned checkpoint); see tools/pattern_classifier.py.
"""
from datetime import datetime, timedelta, timezone

import pytest

from models.bilstm.threshold import OPERATING_THRESHOLD
from orchestrator.state_machine import (
    HYPOTHESIS_IMPLIED_PATTERNS, PATTERN_MIN_CONFIDENCE, REPORT_CONFIDENCE_THRESHOLD, SINGLE_DETECTOR_CONFIDENCE_CAP,
    FlaggedAnomaly, form_hypothesis, investigate,
)
from tools.pattern_classifier import WINDOW_LENGTH, build_pattern_classifier, recent_window

requires_operating_threshold = pytest.mark.skipif(OPERATING_THRESHOLD is None, reason="OPERATING_THRESHOLD is None")

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def track(n: int = 30) -> dict:
    return {"positions": [
        {"received_at": T0 + timedelta(minutes=i), "latitude": 51.0 + i * 0.0028, "longitude": 4.0,
         "sog_knots": 10.0, "cog_deg": 0.0}
        for i in range(n)
    ]}


def anomaly_at(index: int, votes=frozenset(), score: float | None = None) -> FlaggedAnomaly:
    score = (OPERATING_THRESHOLD or 0.0) + 1e-6 if score is None else score
    return FlaggedAnomaly(1, T0 + timedelta(minutes=index), score, "position", 10, 20, detector_votes=votes)


async def _get_track(_anomaly) -> dict:
    return track()


def laya_says(label: str, confidence: float):
    def predict(_state: str) -> dict:
        return {"label": label, "confidence": confidence, "probabilities": {label: confidence}}
    return predict


def classifier_evidence(label: str, confidence: float, available: bool = True) -> dict:
    return {"pattern_classifier": {"available": available, "pattern": label, "confidence": confidence}}


def base_evidence(**extra) -> dict:
    return {"jamming_zones": {"matched": False}, "incident_history": {"same_vessel": [], "same_pattern_elsewhere": []}, **extra}


# --- the tool ---------------------------------------------------------------------------

def test_recent_window_ends_at_the_flagged_report() -> None:
    rows = track()["positions"]
    window = recent_window(rows, T0 + timedelta(minutes=24))
    assert len(window) == WINDOW_LENGTH
    assert window[-1]["received_at"] == T0 + timedelta(minutes=24)


@pytest.mark.asyncio
async def test_stub_without_a_model_is_neutral() -> None:
    result = await build_pattern_classifier(_get_track, predict=None)(anomaly_at(25))
    assert result["available"] is False and "GHAST_LAYA_MODEL" in result["reason"]


@pytest.mark.asyncio
async def test_prediction_is_returned_with_confidence() -> None:
    result = await build_pattern_classifier(_get_track, laya_says("teleport_jump", 0.91))(anomaly_at(25))
    assert result["available"] is True
    assert result["pattern"] == "teleport_jump" and result["confidence"] == pytest.approx(0.91)


@pytest.mark.asyncio
async def test_predictor_receives_the_shared_summary_text() -> None:
    seen = []
    def predict(state: str) -> dict:
        seen.append(state)
        return {"label": "normal_track", "confidence": 0.9, "probabilities": {}}
    await build_pattern_classifier(_get_track, predict)(anomaly_at(25))
    assert seen and seen[0].startswith("reports: 20,")


@pytest.mark.asyncio
async def test_too_little_history_is_unavailable_not_a_guess() -> None:
    result = await build_pattern_classifier(_get_track, laya_says("teleport_jump", 0.99))(anomaly_at(5))
    assert result["available"] is False and "need 20" in result["reason"]


@pytest.mark.asyncio
async def test_predictor_crash_never_escapes() -> None:
    def boom(_state: str) -> dict:
        raise RuntimeError("model exploded")
    result = await build_pattern_classifier(_get_track, boom)(anomaly_at(25))
    assert result["available"] is False and "RuntimeError" in result["reason"]


# --- effect on form_hypothesis ----------------------------------------------------------

@requires_operating_threshold
def test_confident_agreement_lifts_the_single_detector_cap() -> None:
    anomaly = anomaly_at(25, votes=frozenset({"prediction_error"}))
    _, capped = form_hypothesis(anomaly, base_evidence())
    _, lifted = form_hypothesis(anomaly, base_evidence(**classifier_evidence("gradual_drift", 0.9)))
    assert capped == SINGLE_DETECTOR_CONFIDENCE_CAP
    assert lifted == 0.72 and lifted >= REPORT_CONFIDENCE_THRESHOLD


@requires_operating_threshold
def test_confident_normal_track_caps_even_multi_detector_flags() -> None:
    anomaly = anomaly_at(25, votes=frozenset({"prediction_error", "speed_jump"}))
    _, plain = form_hypothesis(anomaly, base_evidence())
    hypothesis, contradicted = form_hypothesis(anomaly, base_evidence(**classifier_evidence("normal_track", 0.95)))
    assert plain == 0.72
    assert hypothesis == "targeted_spoof"  # the classifier never picks or changes the hypothesis
    assert contradicted == SINGLE_DETECTOR_CONFIDENCE_CAP < REPORT_CONFIDENCE_THRESHOLD


@requires_operating_threshold
def test_low_confidence_or_unavailable_classifier_changes_nothing() -> None:
    anomaly = anomaly_at(25, votes=frozenset({"prediction_error"}))
    _, baseline = form_hypothesis(anomaly, base_evidence())
    for evidence in (
        classifier_evidence("gradual_drift", PATTERN_MIN_CONFIDENCE - 0.01),
        classifier_evidence("normal_track", PATTERN_MIN_CONFIDENCE - 0.01),
        classifier_evidence("gradual_drift", 0.99, available=False),
    ):
        assert form_hypothesis(anomaly, base_evidence(**evidence))[1] == baseline


@requires_operating_threshold
def test_classifier_never_turns_a_below_threshold_window_into_a_spoof_or_benign() -> None:
    quiet = anomaly_at(25, score=OPERATING_THRESHOLD / 10)
    assert form_hypothesis(quiet, base_evidence(**classifier_evidence("teleport_jump", 0.99)))[0] == "benign"
    assert form_hypothesis(quiet, base_evidence(**classifier_evidence("normal_track", 0.99)))[0] == "benign"


@requires_operating_threshold
def test_jamming_and_sequence_corroborated_freeze_ignore_a_contradiction() -> None:
    anomaly = anomaly_at(25, votes=frozenset({"prediction_error", "freeze_replay"}))
    contradiction = classifier_evidence("normal_track", 0.99)
    jam = form_hypothesis(anomaly, {**base_evidence(**contradiction), "jamming_zones": {"matched": True}})
    assert jam == ("jamming", 0.85)
    freeze = form_hypothesis(anomaly, base_evidence(**contradiction, freeze_corroboration={"matched": True}))
    assert freeze == ("freeze_replay", 0.8)


@requires_operating_threshold
def test_laya_agrees_only_when_the_label_matches_the_hypothesis() -> None:
    # freeze_replay hypothesis (sequence-corroborated) is capped down to one detector; only a
    # freeze_replay label lifts it, a different confident spoofing label does not.
    anomaly = anomaly_at(25, votes=frozenset({"prediction_error"}))
    freeze = {"freeze_corroboration": {"matched": True}}
    _, wrong_label = form_hypothesis(anomaly, base_evidence(**freeze, **classifier_evidence("teleport_jump", 0.95)))
    _, right_label = form_hypothesis(anomaly, base_evidence(**freeze, **classifier_evidence("freeze_replay", 0.95)))
    assert wrong_label == SINGLE_DETECTOR_CONFIDENCE_CAP
    assert right_label == 0.8


@requires_operating_threshold
def test_freeze_replay_label_does_not_lift_the_cap_on_a_targeted_spoof_hypothesis() -> None:
    anomaly = anomaly_at(25, votes=frozenset({"prediction_error"}))
    hypothesis, confidence = form_hypothesis(anomaly, base_evidence(**classifier_evidence("freeze_replay", 0.95)))
    assert hypothesis == "targeted_spoof"
    assert confidence == SINGLE_DETECTOR_CONFIDENCE_CAP


@requires_operating_threshold
def test_nothing_in_laya_agrees_with_equipment_fault() -> None:
    anomaly = anomaly_at(25, votes=frozenset({"prediction_error"}))
    history = {"same_vessel": [{"id": "a"}], "same_pattern_elsewhere": []}
    evidence = {**base_evidence(**classifier_evidence("gradual_drift", 0.95)), "incident_history": history}
    assert form_hypothesis(anomaly, evidence) == ("equipment_fault", SINGLE_DETECTOR_CONFIDENCE_CAP)


def test_every_implied_label_is_a_real_laya_label() -> None:
    from features.summary import PATTERN_LABELS
    for hypothesis, labels in HYPOTHESIS_IMPLIED_PATTERNS.items():
        assert labels <= set(PATTERN_LABELS), hypothesis


# --- investigate() wiring ---------------------------------------------------------------

async def _const(value):
    async def call(_): return value
    return call


async def _tools(pattern_tool=None) -> dict:
    tools = {"track_history": await _const({"positions": []}), "jamming_zones": await _const({"matched": False}),
             "incident_history": await _const({"same_vessel": [], "same_pattern_elsewhere": []})}
    if pattern_tool is not None:
        tools["pattern_classifier"] = pattern_tool
    return tools


@pytest.mark.asyncio
async def test_investigate_records_classifier_result_in_evidence_and_log() -> None:
    saved = []
    async def persist(row): saved.append(row)
    tool = build_pattern_classifier(_get_track, laya_says("freeze_replay", 0.88))
    result = await investigate(anomaly_at(25), await _tools(tool), persist)
    assert result.evidence["pattern_classifier"]["pattern"] == "freeze_replay"
    assert [entry["tool"] for entry in saved[0]["tool_call_log"]][-1] == "pattern_classifier"


@pytest.mark.asyncio
async def test_investigate_survives_a_classifier_tool_that_raises() -> None:
    saved = []
    async def persist(row): saved.append(row)
    async def broken(_anomaly): raise RuntimeError("laya import failed")
    result = await investigate(anomaly_at(25), await _tools(broken), persist)
    assert result.evidence["pattern_classifier"]["available"] is False
    assert saved  # the incident was still persisted


@pytest.mark.asyncio
async def test_investigate_without_the_tool_is_unchanged() -> None:
    saved = []
    async def persist(row): saved.append(row)
    result = await investigate(anomaly_at(25), await _tools(), persist)
    assert "pattern_classifier" not in result.evidence
    assert len(saved[0]["tool_call_log"]) == 3
