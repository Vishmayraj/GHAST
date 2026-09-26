import pytest
from datetime import datetime, timezone
from orchestrator.state_machine import FlaggedAnomaly, InvestigationState, investigate

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
