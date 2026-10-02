import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from review import (
    LIST_QUERY, SHOW_QUERY, VERDICT_QUERY, VERDICTS, format_list, format_show, get_incident,
    list_unreviewed, main, record_verdict, window_summary,
)

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
INCIDENT_ID = str(uuid.uuid4())


def stored_positions(count: int = 30) -> list[dict]:
    # evidence is stored as JSON with default=str, so times come back as strings
    return [
        {"received_at": str(T0 + timedelta(minutes=i)), "latitude": 51.0 + i * 0.0028, "longitude": 4.0,
         "sog_knots": 10.0, "cog_deg": 0.0}
        for i in range(count)
    ]


def incident_row(**overrides) -> dict:
    row = {
        "id": INCIDENT_ID, "mmsi": 123, "flagged_at": T0 + timedelta(minutes=25),
        "window_start": T0 + timedelta(minutes=6), "window_end": T0 + timedelta(minutes=25),
        "hypothesis": "targeted_spoof", "confidence": 0.72, "status": "reported",
        "anomaly_type": "prediction_error+speed_jump", "anomaly_score": 0.0123, "has_report": True,
        "evidence": json.dumps({"track_history": {"positions": stored_positions()}, "jamming_zones": {"matched": False}}),
        "report_text": "drafted text", "review_verdict": None, "reviewed_by": None, "reviewed_at": None, "review_notes": None,
    }
    return {**row, **overrides}


class FakeDb:
    def __init__(self, row: dict | None = None, rows: list[dict] | None = None) -> None:
        self.row = row
        self.rows = rows or []
        self.calls: list[tuple] = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        if query == SHOW_QUERY:
            return self.row
        return {"id": INCIDENT_ID}


@pytest.mark.asyncio
async def test_list_asks_only_for_unreviewed_tier_b_first_then_priority_then_newest() -> None:
    db = FakeDb(rows=[incident_row()])
    rows = await list_unreviewed(db, limit=5)
    assert db.calls == [(LIST_QUERY, (5,))]
    assert "review_verdict IS NULL" in LIST_QUERY and "ORDER BY tier DESC, priority DESC NULLS LAST, flagged_at DESC" in LIST_QUERY
    assert rows[0]["id"] == INCIDENT_ID


def test_list_output_shows_hypothesis_confidence_votes_span_and_report_pointer() -> None:
    text = format_list([incident_row()])
    for expected in (INCIDENT_ID, "mmsi=123", "hypothesis=targeted_spoof", "confidence=0.72",
                     "votes=prediction_error+speed_jump", "2026-09-01 00:06:00 to 2026-09-01 00:25:00",
                     f"python review.py show {INCIDENT_ID}"):
        assert expected in text


def test_list_says_what_state_the_report_is_in() -> None:
    assert "stored" in format_list([incident_row(has_report=True)])
    assert f"none yet (draft one: python review.py report {INCIDENT_ID})" in format_list([incident_row(has_report=False)])
    expired = format_list([incident_row(has_report=False, report_delete_reason="expired")])
    deleted = format_list([incident_row(has_report=False, report_delete_reason="analyst")])
    assert "expired after 24h" in expired and "deleted by an analyst" in deleted


def test_every_incident_can_get_a_report_whatever_its_confidence() -> None:
    low = format_list([incident_row(has_report=False, confidence=0.4)])
    assert "can be drafted" not in low and f"python review.py report {INCIDENT_ID}" in low


def test_list_shows_tier_and_priority() -> None:
    text = format_list([incident_row(tier="B", priority=0.91)])
    assert text.startswith("[B] priority=0.91 ")
    assert format_list([incident_row()]).startswith("[A] ")


def test_show_prints_the_challenger_and_a_failed_verification() -> None:
    row = incident_row(challenge=json.dumps({"benign_likelihood": 0.6, "argument": "vessel was at anchor"}),
                       report_verification=json.dumps({"verdict": "fail", "issues": ["wrong MMSI"]}))
    text = format_show(row)
    assert "challenger: benign_likelihood=0.6" in text and "report verification FAILED: wrong MMSI" in text


def test_show_says_when_a_report_expired() -> None:
    assert "expired after 24h" in format_show(incident_row(report_text=None, report_delete_reason="expired"))


def test_delete_report_command_parses() -> None:
    with pytest.raises(SystemExit):
        main(["--dsn", "postgresql://x", "delete-report", "not-a-uuid"])


def test_report_command_parses() -> None:
    with pytest.raises(SystemExit):
        main(["--dsn", "postgresql://x", "report", "not-a-uuid"])


def test_list_output_copes_with_old_rows_that_have_no_window_or_report() -> None:
    text = format_list([incident_row(window_start=None, window_end=None, has_report=False, confidence=None)])
    assert "span=n/a to n/a" in text and "report: none" in text and "confidence=n/a" in text


def test_empty_list_says_so() -> None:
    assert format_list([]) == "no unreviewed incidents"


def test_window_summary_rebuilds_from_stored_string_timestamps() -> None:
    summary = window_summary(json.loads(incident_row()["evidence"]), T0 + timedelta(minutes=25))
    assert isinstance(summary, str) and summary and not summary.startswith("unavailable")


def test_window_summary_says_when_the_stored_track_is_too_short() -> None:
    evidence = {"track_history": {"positions": stored_positions(5)}}
    assert window_summary(evidence, T0 + timedelta(minutes=4)).startswith("unavailable: only 5 stored reports")


def test_show_output_has_evidence_summary_and_report_without_the_raw_position_list() -> None:
    text = format_show(incident_row())
    assert "jamming_zones" in text and "position_count" in text and "30" in text
    assert "20-report summary" in text and "drafted text" in text
    assert text.count('"latitude"') == 2  # first and last position only


@pytest.mark.asyncio
async def test_get_incident_returns_none_for_an_unknown_id() -> None:
    assert await get_incident(FakeDb(row=None), INCIDENT_ID) is None


@pytest.mark.asyncio
async def test_verdict_resolves_the_incident_and_records_who_and_why() -> None:
    db = FakeDb(row=incident_row())
    outcome = await record_verdict(db, INCIDENT_ID, "confirmed_spoof", "asha", "matches a known replay")
    assert outcome == "recorded"
    query, args = db.calls[-1]
    assert query == VERDICT_QUERY
    assert "status = 'resolved'" in query and "reviewed_at = now()" in query
    assert args == (INCIDENT_ID, "confirmed_spoof", "asha", "matches a known replay", False)


@pytest.mark.asyncio
async def test_an_existing_verdict_is_not_replaced_without_force() -> None:
    db = FakeDb(row=incident_row(review_verdict="benign"))
    assert await record_verdict(db, INCIDENT_ID, "jamming", "asha") == "already_reviewed"
    assert all(query != VERDICT_QUERY for query, _ in db.calls)
    assert await record_verdict(db, INCIDENT_ID, "jamming", "asha", force=True) == "recorded"
    assert db.calls[-1][1][-1] is True


@pytest.mark.asyncio
async def test_verdict_on_a_missing_incident_changes_nothing() -> None:
    db = FakeDb(row=None)
    assert await record_verdict(db, INCIDENT_ID, "benign", "asha") == "not_found"
    assert all(query != VERDICT_QUERY for query, _ in db.calls)


@pytest.mark.asyncio
async def test_an_unknown_verdict_is_rejected_before_touching_the_database() -> None:
    db = FakeDb(row=incident_row())
    with pytest.raises(ValueError):
        await record_verdict(db, INCIDENT_ID, "probably_fine", "asha")
    assert db.calls == []


def test_verdicts_match_the_schema_check() -> None:
    import pathlib
    schema = (pathlib.Path(__file__).resolve().parents[2] / "backend" / "models" / "schema.sql").read_text()
    for verdict in VERDICTS:
        assert f"'{verdict}'" in schema


def test_cli_rejects_a_bad_id_and_a_bad_verdict() -> None:
    with pytest.raises(SystemExit):
        main(["--dsn", "postgresql://x", "show", "not-a-uuid"])
    with pytest.raises(SystemExit):
        main(["--dsn", "postgresql://x", "verdict", INCIDENT_ID, "probably_fine"])
