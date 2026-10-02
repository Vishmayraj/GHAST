import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from agents import pipeline as pl
from agents.pipeline import IncidentPipeline
from orchestrator.state_machine import FlaggedAnomaly, InvestigationResult, InvestigationState

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


class Db:
    def __init__(self, tier="A", exists=True):
        self.tier, self.exists, self.writes, self.runs = tier, exists, [], []

    async def fetchrow(self, query, *args):
        if query == pl.LOAD_QUERY:
            if not self.exists:
                return None
            return {"id": "i1", "mmsi": 111, "flagged_at": T0, "latitude": 51.0, "longitude": 4.0, "anomaly_score": 2.0,
                    "tier": self.tier, "hypothesis": "jamming", "confidence": 0.85,
                    "evidence": json.dumps({"track_history": {"positions": [{"sog_knots": 9.0}] * 20}})}
        if "count(*) AS open_a" in query:
            return {"open_a": 0}
        return None

    async def fetch(self, query, *args):
        return [{"km": 1.0}] * 5 if "FROM vessel_position" in query else []

    async def execute(self, query, *args):
        (self.writes if query in (pl.STORE_FLEET_QUERY, pl.STORE_CHALLENGE_QUERY, pl.STORE_PRIORITY_QUERY) else self.runs).append((query, args))


def anomaly(tier="A"):
    return FlaggedAnomaly(111, T0, 2.0, "prediction_error", 51.0, 4.0, detector_votes=frozenset({"prediction_error"}), tier=tier, threshold_a=0.2)


def result(incident_id="i1"):
    return InvestigationResult(InvestigationState.DONE, "jamming", 0.85, {}, [], incident_id)


@pytest.mark.asyncio
async def test_all_three_agents_store_their_output_and_are_audited(monkeypatch):
    db = Db()
    await IncidentPipeline(db, None, None)(anomaly(), result())
    stored = {q for q, _ in db.writes}
    assert stored == {pl.STORE_FLEET_QUERY, pl.STORE_CHALLENGE_QUERY, pl.STORE_PRIORITY_QUERY}
    assert sorted(a[0] for _, a in db.runs) == ["challenger_agent", "fleet_context_agent", "triage_agent"]
    assert all(a[1] == "fallback" for _, a in db.runs)  # no LLM configured
    priority = [a for q, a in db.writes if q == pl.STORE_PRIORITY_QUERY][0]
    assert priority[0] == "i1" and 0 <= priority[1] <= 1


@pytest.mark.asyncio
async def test_tier_a_never_drafts_a_report_but_tier_b_does(monkeypatch):
    drafted = []

    async def fake_generate(db, incident_id, client, **kw):
        drafted.append((incident_id, kw["auto"]))
        return NS(outcome="generated")
    monkeypatch.setattr(pl, "generate_report", fake_generate)
    await IncidentPipeline(Db("A"), object(), "m")(anomaly("A"), result())
    assert drafted == []
    await IncidentPipeline(Db("B"), object(), "m")(anomaly("B"), result())
    assert drafted == [("i1", True)]


@pytest.mark.asyncio
async def test_tier_b_without_an_llm_logs_one_line_and_still_orders_the_queue(caplog):
    db = Db("B")
    p = IncidentPipeline(db, None, None)
    with caplog.at_level("WARNING", logger="ghast.agent.pipeline"):
        await p(anomaly("B"), result())
        await p(anomaly("B"), result())
    assert sum(r.getMessage().startswith("llm missing") for r in caplog.records) == 1
    assert any(q == pl.STORE_PRIORITY_QUERY for q, _ in db.writes)


@pytest.mark.asyncio
async def test_a_bug_that_stops_an_agent_being_built_is_loud_not_swallowed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("fleet exploded")
    monkeypatch.setattr(pl, "build_fleet_context_agent", boom)
    db = Db()
    with pytest.raises(RuntimeError):  # construction failing is a programming error and must be loud
        await IncidentPipeline(db, None, None)(anomaly(), result())


@pytest.mark.asyncio
async def test_a_failing_agent_run_is_isolated(monkeypatch):
    real = pl.build_challenger_agent

    def wrapped(*a, **k):
        agent = real(*a, **k)

        async def broken(task):
            raise RuntimeError("challenger exploded")
        agent.run = broken
        return agent
    monkeypatch.setattr(pl, "build_challenger_agent", wrapped)
    db = Db()
    await IncidentPipeline(db, None, None)(anomaly(), result())
    stored = {q for q, _ in db.writes}
    assert pl.STORE_CHALLENGE_QUERY not in stored and {pl.STORE_FLEET_QUERY, pl.STORE_PRIORITY_QUERY} <= stored


@pytest.mark.asyncio
async def test_no_incident_id_or_missing_row_is_a_quiet_no_op():
    db = Db()
    await IncidentPipeline(db, None, None)(anomaly(), result(None))
    await IncidentPipeline(Db(exists=False), None, None)(anomaly(), result())
    assert db.writes == [] and db.runs == []
