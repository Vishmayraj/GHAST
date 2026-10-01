import json
import uuid

import pytest

from orchestrator.state_machine import REPORT_DRAFT_CONFIDENCE_THRESHOLD
from report_generator.on_demand import SHOW_QUERY, STORE_QUERY, generate_report, is_report_eligible

INCIDENT_ID = str(uuid.uuid4())


def incident(**overrides) -> dict:
    row = {"id": INCIDENT_ID, "mmsi": 123, "flagged_at": None, "anomaly_score": 0.9, "anomaly_type": "prediction_error",
           "hypothesis": "jamming", "confidence": 0.85, "status": "reported",
           "evidence": json.dumps({"track_history": {"positions": []}}), "report_text": None}
    return {**row, **overrides}


class FakeDb:
    def __init__(self, row, store_wins: bool = True) -> None:
        self.row, self.store_wins, self.calls = row, store_wins, []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        if query == SHOW_QUERY:
            return self.row
        return {"id": INCIDENT_ID} if self.store_wins else None


class FakeCompletions:
    def __init__(self, text="# brief", error=None) -> None:
        self.text, self.error, self.requests = text, error, []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        message = type("M", (), {"content": self.text})()
        return type("C", (), {"choices": [type("Ch", (), {"message": message})()]})()


def fake_client(**kwargs):
    completions = FakeCompletions(**kwargs)
    return type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})(), completions


def stored_queries(db):
    return [query for query, _ in db.calls if query == STORE_QUERY]


def test_eligibility_is_the_draft_threshold_and_inclusive() -> None:
    assert is_report_eligible(REPORT_DRAFT_CONFIDENCE_THRESHOLD)
    assert not is_report_eligible(REPORT_DRAFT_CONFIDENCE_THRESHOLD - 0.001)
    assert not is_report_eligible(None)


@pytest.mark.asyncio
async def test_an_eligible_incident_is_drafted_and_stored() -> None:
    db, (client, completions) = FakeDb(incident()), fake_client()
    result = await generate_report(db, INCIDENT_ID, client)
    assert (result.outcome, result.report_text) == ("generated", "# brief")
    assert len(completions.requests) == 1
    assert db.calls[-1] == (STORE_QUERY, (INCIDENT_ID, "# brief", False))


@pytest.mark.asyncio
async def test_a_below_threshold_incident_costs_no_model_call() -> None:
    db, (client, completions) = FakeDb(incident(confidence=0.72)), fake_client()
    result = await generate_report(db, INCIDENT_ID, client)
    assert result.outcome == "not_eligible" and "0.8" in result.detail
    assert completions.requests == [] and stored_queries(db) == []


@pytest.mark.asyncio
async def test_a_stored_report_is_returned_without_a_model_call() -> None:
    db, (client, completions) = FakeDb(incident(report_text="old brief")), fake_client()
    result = await generate_report(db, INCIDENT_ID, client)
    assert (result.outcome, result.report_text) == ("already_drafted", "old brief")
    assert completions.requests == [] and stored_queries(db) == []


@pytest.mark.asyncio
async def test_force_drafts_again_and_overwrites() -> None:
    db, (client, completions) = FakeDb(incident(report_text="old brief")), fake_client(text="new brief")
    result = await generate_report(db, INCIDENT_ID, client, force=True)
    assert (result.outcome, result.report_text) == ("generated", "new brief")
    assert db.calls[-1] == (STORE_QUERY, (INCIDENT_ID, "new brief", True))


@pytest.mark.asyncio
async def test_an_unknown_incident_changes_nothing() -> None:
    db, (client, completions) = FakeDb(None), fake_client()
    assert (await generate_report(db, INCIDENT_ID, client)).outcome == "not_found"
    assert completions.requests == []


@pytest.mark.asyncio
async def test_a_provider_error_stores_nothing() -> None:
    db, (client, _) = FakeDb(incident()), fake_client(error=RuntimeError("rate limited"))
    result = await generate_report(db, INCIDENT_ID, client)
    assert result.outcome == "failed" and "rate limited" in result.detail
    assert stored_queries(db) == []


@pytest.mark.asyncio
async def test_an_empty_provider_response_is_not_stored_as_a_report() -> None:
    db, (client, _) = FakeDb(incident()), fake_client(text="")
    assert (await generate_report(db, INCIDENT_ID, client)).outcome == "failed"
    assert stored_queries(db) == []


@pytest.mark.asyncio
async def test_losing_a_concurrent_draft_returns_the_winning_text() -> None:
    class RacingDb(FakeDb):
        async def fetchrow(self, query, *args):
            if query == SHOW_QUERY and any(q == STORE_QUERY for q, _ in self.calls):
                self.calls.append((query, args))
                return incident(report_text="winner")
            return await super().fetchrow(query, *args)
    db, (client, _) = RacingDb(incident(), store_wins=False), fake_client()
    result = await generate_report(db, INCIDENT_ID, client)
    assert (result.outcome, result.report_text) == ("already_drafted", "winner")
