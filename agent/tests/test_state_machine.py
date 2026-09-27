import pytest
from datetime import datetime, timezone
from orchestrator.state_machine import FlaggedAnomaly, InvestigationState, form_hypothesis, investigate
from models.bilstm.threshold import OPERATING_THRESHOLD

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

def test_score_just_below_threshold_is_benign() -> None:
    assert OPERATING_THRESHOLD is not None, "OPERATING_THRESHOLD must be finalized before this test is meaningful"
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD - 1e-6, "position", 10, 20)
    hypothesis, confidence = form_hypothesis(anomaly, _NEUTRAL_EVIDENCE)
    assert hypothesis == "benign"

def test_score_just_above_threshold_is_not_benign() -> None:
    assert OPERATING_THRESHOLD is not None, "OPERATING_THRESHOLD must be finalized before this test is meaningful"
    anomaly = FlaggedAnomaly(1, datetime.now(timezone.utc), OPERATING_THRESHOLD + 1e-6, "position", 10, 20)
    hypothesis, confidence = form_hypothesis(anomaly, _NEUTRAL_EVIDENCE)
    assert hypothesis != "benign"
