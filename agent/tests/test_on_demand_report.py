import json
import uuid

import pytest

from report_generator.on_demand import (
    DELETE_QUERY, MAX_DRAFT_ATTEMPTS, PURGE_QUERY, REPORT_TTL_HOURS, SHOW_QUERY, STORE_QUERY,
    delete_report, generate_report, purge_expired_reports,
)

INCIDENT_ID = str(uuid.uuid4())
GOOD = "# GHAST // INCIDENT BRIEF\n> `jamming` · `85%` · `reported`\n**MMSI:** 123"


def incident(**overrides) -> dict:
    row = {"id": INCIDENT_ID, "mmsi": 123, "flagged_at": None, "anomaly_score": 0.9, "anomaly_type": "prediction_error",
           "hypothesis": "jamming", "confidence": 0.85, "status": "reported", "tier": "A", "review_verdict": None,
           "evidence": json.dumps({"track_history": {"positions": []}}), "challenge": None, "report_text": None}
    return {**row, **overrides}


class FakeDb:
    def __init__(self, row, store_wins: bool = True) -> None:
        self.row, self.store_wins, self.calls, self.runs = row, store_wins, [], []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        if query == SHOW_QUERY:
            return self.row
        if query == DELETE_QUERY:
            return {"id": INCIDENT_ID} if self.row and self.row.get("report_text") else None
        return {"id": INCIDENT_ID} if self.store_wins else None

    async def execute(self, query, *args):
        if query == PURGE_QUERY:
            return "UPDATE 3"
        self.runs.append(args)  # agent_runs audit rows


class FakeCompletions:
    def __init__(self, *texts, error=None) -> None:
        self.texts, self.error, self.requests = list(texts) or [GOOD], error, []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        message = type("M", (), {"content": text})()
        return type("C", (), {"choices": [type("Ch", (), {"message": message})()]})()


def fake_client(*texts, **kwargs):
    completions = FakeCompletions(*texts, **kwargs)
    return type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})(), completions


def stores(db):
    return [args for query, args in db.calls if query == STORE_QUERY]


@pytest.mark.asyncio
async def test_a_tier_a_incident_is_drafted_on_request_whatever_its_confidence() -> None:
    db, (client, completions) = FakeDb(incident(confidence=0.55, hypothesis="equipment_fault")), fake_client("equipment_fault, MMSI 123, 55%")
    result = await generate_report(db, INCIDENT_ID, client)
    assert result.outcome == "generated" and result.verified is True
    assert len(completions.requests) == 1
    _id, text, force, auto, ttl, verification = stores(db)[0]
    assert (force, auto, ttl) == (False, False, REPORT_TTL_HOURS) and json.loads(verification)["verdict"] == "pass"


@pytest.mark.asyncio
async def test_auto_reports_are_marked_auto() -> None:
    db, (client, _) = FakeDb(incident(tier="B")), fake_client()
    await generate_report(db, INCIDENT_ID, client, auto=True)
    assert stores(db)[0][3] is True


@pytest.mark.asyncio
async def test_a_stored_report_is_returned_without_a_model_call() -> None:
    db, (client, completions) = FakeDb(incident(report_text="old brief")), fake_client()
    result = await generate_report(db, INCIDENT_ID, client)
    assert (result.outcome, result.report_text) == ("already_drafted", "old brief")
    assert completions.requests == [] and stores(db) == []


@pytest.mark.asyncio
async def test_force_drafts_again_and_overwrites() -> None:
    db, (client, _) = FakeDb(incident(report_text="old brief")), fake_client()
    result = await generate_report(db, INCIDENT_ID, client, force=True)
    assert result.outcome == "generated" and stores(db)[0][2] is True


@pytest.mark.asyncio
async def test_a_draft_that_fails_verification_is_redrafted_once_with_the_issues() -> None:
    bad = "# brief\njamming at 40%, MMSI 123"  # wrong confidence
    db, (client, completions) = FakeDb(incident()), fake_client(bad, GOOD)
    result = await generate_report(db, INCIDENT_ID, client)
    assert result.outcome == "generated" and result.verified is True and len(completions.requests) == 2
    assert "stored confidence is 85%" in completions.requests[1]["messages"][0]["content"]
    assert json.loads(stores(db)[0][5])["attempts"] == 2


@pytest.mark.asyncio
async def test_a_draft_that_never_verifies_is_stored_with_a_visible_warning() -> None:
    bad = "# brief\nstatus is OPEN for 999"
    db, (client, completions) = FakeDb(incident()), fake_client(bad)
    result = await generate_report(db, INCIDENT_ID, client)
    assert result.outcome == "generated" and result.verified is False and len(completions.requests) == MAX_DRAFT_ATTEMPTS
    assert result.report_text.startswith("> WARNING: this draft did not pass verification")
    assert json.loads(stores(db)[0][5])["verdict"] == "fail"


@pytest.mark.asyncio
async def test_every_verification_is_audited() -> None:
    db, (client, _) = FakeDb(incident()), fake_client()
    await generate_report(db, INCIDENT_ID, client)
    assert db.runs and db.runs[0][0] == "report_verifier_agent"


@pytest.mark.asyncio
async def test_an_unknown_incident_changes_nothing() -> None:
    db, (client, completions) = FakeDb(None), fake_client()
    assert (await generate_report(db, INCIDENT_ID, client)).outcome == "not_found"
    assert completions.requests == []


@pytest.mark.asyncio
async def test_a_provider_error_stores_nothing() -> None:
    db, (client, _) = FakeDb(incident()), fake_client(error=RuntimeError("rate limited"))
    result = await generate_report(db, INCIDENT_ID, client)
    assert result.outcome == "failed" and "rate limited" in result.detail and stores(db) == []


@pytest.mark.asyncio
async def test_an_empty_provider_response_is_not_stored_as_a_report() -> None:
    db, (client, _) = FakeDb(incident()), fake_client("")
    assert (await generate_report(db, INCIDENT_ID, client)).outcome == "failed"
    assert stores(db) == []


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


@pytest.mark.asyncio
async def test_purge_reports_how_many_expired_and_delete_only_hits_live_reports() -> None:
    assert await purge_expired_reports(FakeDb(None)) == 3
    assert await delete_report(FakeDb(incident(report_text="x")), INCIDENT_ID) is True
    assert await delete_report(FakeDb(incident()), INCIDENT_ID) is False


def test_purge_and_delete_keep_the_incident_and_record_why_the_text_went() -> None:
    for query, reason in ((PURGE_QUERY, "expired"), (DELETE_QUERY, "analyst")):
        assert "report_text = NULL" in query and f"'{reason}'" in query and "DELETE FROM" not in query.upper()
