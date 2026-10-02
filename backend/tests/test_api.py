import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import shaping

KEY = "test-key"
H = {"X-API-Key": KEY}
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
ID = str(uuid.uuid4())


class FakePool:
    """Answers by which query constant was passed, and records every call."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def _answer(self, query, args):
        self.calls.append((query, args))
        for name in ("HEALTH", "LIST", "DETAIL", "INCIDENT_TRACK", "VESSEL_TRACK", "ZONES", "THRESHOLDS", "REVIEW", "EXISTS"):
            if query is getattr(main, f"{name}_QUERY"):
                value = self.answers.get(name)
                return value(*args) if callable(value) else value
        value = self.answers.get("REVIEWED")
        return value if value is not None else []

    async def fetch(self, query, *args):
        return self._answer(query, args) or []

    async def fetchrow(self, query, *args):
        return self._answer(query, args)


def summary_row(i=0, **over):
    row = {"id": str(uuid.uuid4()), "mmsi": 244710820 + i, "vessel_name": None, "flagged_at": NOW - timedelta(minutes=i),
           "hypothesis": "targeted_spoof", "confidence": 0.72, "status": "escalated", "tier": "A", "priority": None,
           "anomaly_type": "prediction_error", "review_verdict": None, "reviewed_by": None, "reviewed_at": None}
    row.update(over)
    return row


def detail_row(**over):
    evidence = {
        "detector_corroboration": {"votes": ["prediction_error"], "count": 1},
        "freeze_corroboration": {"matched": False, "frozen_reports": 0, "total_pairs": 19},
        "jamming_zones": {"matched": False, "zone": None},
        "incident_history": {"same_vessel": [], "same_pattern_elsewhere": [{"id": "x", "mmsi": 999000111, "hypothesis": "benign"}]},
        "track_history": {"positions": [{"latitude": 1.0, "longitude": 2.0}] * 20},
        "pattern_classifier": {"available": True, "pattern": "teleport_jump", "confidence": 0.88, "probabilities": {}},
        "fleet_context": {"scope": "isolated", "neighbours_checked": 7, "nearby_incidents": 0, "note": "7 neighbours reporting, none flagged."},
    }
    row = summary_row(id=ID, **over)
    row.update({"window_start": NOW - timedelta(hours=1), "window_end": NOW, "anomaly_score": 0.0187, "evidence": json.dumps(evidence),
                "tool_call_log": [{"tool": "track_history", "result": {"positions": [1, 2, 3]}},
                                  {"tool": "incident_history", "result": {"same_vessel": [], "same_pattern_elsewhere": [{"mmsi": 999000111}]}}],
                "report_text": None, "report_expires_at": None, "report_verification": None,
                "challenge": json.dumps({"benign_likelihood": 0.2, "argument": "Weak case.", "cited": []}), "review_notes": None})
    return row


def client(pool, key=KEY):
    return TestClient(main.create_app(pool=pool, api_key=key))


def test_refuses_to_start_without_a_key():
    with pytest.raises(RuntimeError):
        main.create_app(pool=FakePool(), api_key="")


@pytest.mark.parametrize("path", ["/health", "/incidents", f"/incidents/{ID}", "/vessels/1/track", "/vessels/1/incidents", "/zones", "/review-stats", "/thresholds"])
def test_every_route_needs_the_key(path):
    c = client(FakePool())
    assert c.get(path).status_code == 401
    assert c.get(path, headers={"X-API-Key": "wrong"}).status_code == 401


def test_review_needs_the_key():
    assert client(FakePool()).post(f"/incidents/{ID}/review", json={"verdict": "benign"}).status_code == 401


def test_health_reports_ages_and_503_when_db_down():
    c = client(FakePool(HEALTH={"position_age": 4.0, "incident_age": None}))
    assert c.get("/health", headers=H).json() == {"database": "ok", "latest_position_age_s": 4.0, "latest_incident_age_s": None}

    def boom(*_):
        raise OSError("down")
    assert client(FakePool(HEALTH=boom)).get("/health", headers=H).status_code == 503


def test_incident_list_passes_filters_and_paginates_by_flagged_at():
    rows = [summary_row(i) for i in range(3)]
    pool = FakePool(LIST=rows)
    body = client(pool).get("/incidents?limit=2&status=escalated&hypothesis=benign&mmsi=5", headers=H).json()
    assert len(body["items"]) == 2
    assert body["next_before"] == rows[1]["flagged_at"].isoformat().replace("+00:00", "Z")
    _, args = pool.calls[0]
    assert args == ("escalated", "benign", 5, None, 3)  # limit + 1 fetched to know there is a next page


def test_last_page_has_no_cursor_and_before_is_forwarded():
    pool = FakePool(LIST=[summary_row(0)])
    body = client(pool).get("/incidents?limit=5&before=2026-10-03T11:00:00Z", headers=H).json()
    assert body["next_before"] is None
    assert pool.calls[0][1][3] == datetime(2026, 10, 3, 11, 0, tzinfo=timezone.utc)


def test_incident_list_rejects_values_outside_the_vocabulary():
    c = client(FakePool(LIST=[]))
    assert c.get("/incidents?status=open", headers=H).status_code == 422
    assert c.get("/incidents?hypothesis=aliens", headers=H).status_code == 422
    assert c.get("/incidents?limit=1000", headers=H).status_code == 422


def test_incident_detail_is_shaped_and_never_leaks_other_vessels():
    track = [{"received_at": NOW - timedelta(minutes=5), "latitude": 51.0, "longitude": 2.0}, {"received_at": NOW, "latitude": 51.1, "longitude": 2.1}]
    r = client(FakePool(DETAIL=detail_row(), INCIDENT_TRACK=track)).get(f"/incidents/{ID}", headers=H)
    assert r.status_code == 200
    d = r.json()
    assert "evidence" in d and all(set(e) == {"text"} for e in d["evidence"])
    assert "999000111" not in r.text  # other vessel's MMSI from same_pattern_elsewhere
    assert [t["flagged"] for t in d["track"]] == [False, True]
    assert d["fleet_context"]["isolated"] is True and d["fleet_context"]["cluster_vessels"] == 1
    assert d["challenge"] == {"benign_likelihood": 0.2, "argument": "Weak case."}
    assert {v["detector"] for v in d["votes"]} == {"Prediction error", "Freeze check", "Pattern classifier"}
    assert d["tool_call_log"][0] == {"tool": "track_history", "summary": "Pulled 3 reports for this vessel up to the flag."}
    assert "tool_call_log" in d and "result" not in json.dumps(d["tool_call_log"])


def test_detail_track_reads_live_rows_only_from_the_stored_window():
    pool = FakePool(DETAIL=detail_row(), INCIDENT_TRACK=[])
    client(pool).get(f"/incidents/{ID}", headers=H)
    query, args = pool.calls[1]
    assert "<> 'historical'" in query
    assert args[1] == NOW - timedelta(hours=1) and args[2] == NOW


def test_detail_falls_back_to_two_hours_when_no_window_was_stored():
    row = detail_row()
    row["window_start"] = None
    pool = FakePool(DETAIL=row, INCIDENT_TRACK=[])
    client(pool).get(f"/incidents/{ID}", headers=H)
    assert pool.calls[1][1][1] == NOW - timedelta(hours=2)


def test_unknown_and_malformed_incident_ids():
    assert client(FakePool(DETAIL=None)).get(f"/incidents/{uuid.uuid4()}", headers=H).status_code == 404
    assert client(FakePool()).get("/incidents/not-a-uuid", headers=H).status_code == 422


def test_vessel_track_defaults_to_live_only_and_can_include_historical():
    rows = [{"received_at": NOW, "latitude": 1.0, "longitude": 2.0, "sog_knots": 3.0, "cog_deg": None}]
    pool = FakePool(VESSEL_TRACK=rows)
    c = client(pool)
    body = c.get("/vessels/244710820/track?to=2026-10-03T12:00:00Z", headers=H).json()
    assert body["mmsi"] == 244710820 and body["points"][0]["sog_knots"] == 3.0
    assert pool.calls[0][1][3] is False
    c.get("/vessels/244710820/track?include_historical=true&from=2026-04-01T00:00:00Z", headers=H)
    assert pool.calls[1][1][3] is True and pool.calls[1][1][1] == datetime(2026, 4, 1, tzinfo=timezone.utc)


def test_vessel_incidents_filters_by_mmsi():
    pool = FakePool(LIST=[summary_row(0)])
    body = client(pool).get("/vessels/244710820/incidents", headers=H).json()
    assert len(body["items"]) == 1 and pool.calls[0][1][2] == 244710820


def test_zones_come_back_as_geojson_and_empty_is_valid():
    assert client(FakePool(ZONES=[])).get("/zones", headers=H).json() == {"type": "FeatureCollection", "features": []}
    zone = {"id": "z1", "name": "Test zone", "confidence": 0.5, "geometry": json.dumps({"type": "MultiPolygon", "coordinates": []})}
    f = client(FakePool(ZONES=[zone])).get("/zones", headers=H).json()["features"][0]
    assert f["properties"]["name"] == "Test zone" and f["geometry"]["type"] == "MultiPolygon"


def test_review_stats_uses_the_cli_logic_and_marks_too_few():
    rows = [{"hypothesis": "targeted_spoof", "anomaly_type": "prediction_error", "review_verdict": "confirmed_spoof"},
            {"hypothesis": "targeted_spoof", "anomaly_type": "prediction_error", "review_verdict": "benign"}]
    body = client(FakePool(REVIEWED=rows)).get("/review-stats", headers=H).json()
    assert body["reviewed"] == 2 and body["minimum"] == 30
    assert body["rows"][0]["hypothesis"] == "targeted_spoof" and body["rows"][0]["precision"] == 0.5
    empty = client(FakePool()).get("/review-stats", headers=H).json()
    assert empty["reviewed"] == 0 and empty["rows"] == []


def test_thresholds_active_is_newest():
    new = {"threshold_a": 0.2, "threshold_b": 0.9, "model_version": "v1", "set_by": "agent", "reason": "r", "created_at": NOW}
    old = dict(new, threshold_a=0.1)
    body = client(FakePool(THRESHOLDS=[new, old])).get("/thresholds", headers=H).json()
    assert body["active"]["threshold_a"] == 0.2 and len(body["history"]) == 1
    assert client(FakePool(THRESHOLDS=[])).get("/thresholds", headers=H).json() == {"active": None, "history": []}


def test_review_records_verdict_and_returns_the_incident():
    resolved = detail_row(status="resolved", review_verdict="benign", reviewed_by="vish")
    pool = FakePool(REVIEW={"id": ID}, DETAIL=resolved)
    r = client(pool).post(f"/incidents/{ID}/review", headers=H, json={"verdict": "benign", "notes": "at anchor", "reviewer": "vish"})
    assert r.status_code == 200 and r.json()["status"] == "resolved" and r.json()["review_verdict"] == "benign"
    assert pool.calls[0][1] == (ID, "benign", "vish", "at anchor")
    assert "status = 'resolved'" in pool.calls[0][0] and "review_verdict IS NULL" in pool.calls[0][0]


def test_review_twice_is_a_conflict_and_unknown_is_404():
    assert client(FakePool(REVIEW=None, EXISTS={"?column?": 1})).post(f"/incidents/{ID}/review", headers=H, json={"verdict": "benign"}).status_code == 409
    assert client(FakePool(REVIEW=None, EXISTS=None)).post(f"/incidents/{ID}/review", headers=H, json={"verdict": "benign"}).status_code == 404


def test_review_rejects_bad_verdicts_and_defaults_the_reviewer():
    c = client(FakePool(REVIEW={"id": ID}, DETAIL=detail_row()))
    assert c.post(f"/incidents/{ID}/review", headers=H, json={"verdict": "maybe"}).status_code == 422
    assert c.post(f"/incidents/{ID}/review", headers=H, json={}).status_code == 422
    pool = FakePool(REVIEW={"id": ID}, DETAIL=detail_row())
    client(pool).post(f"/incidents/{ID}/review", headers=H, json={"verdict": "unclear"})
    assert pool.calls[0][1][2] == "analyst"


def test_shaping_fleet_context_insufficient_is_neither_isolated_nor_area():
    f = shaping.fleet_context_from({"fleet_context": {"scope": "insufficient", "neighbours_checked": 1, "nearby_incidents": 0, "note": "n"}})
    assert f["isolated"] is None and f["cluster_vessels"] == 1
    area = shaping.fleet_context_from({"fleet_context": {"scope": "area", "neighbours_checked": 9, "nearby_incidents": 4, "note": "n"}})
    assert area["isolated"] is False and area["cluster_vessels"] == 5
    assert shaping.fleet_context_from({}) is None
    stored = shaping.fleet_context_from({"fleet_context": {"scope": "area", "neighbours_checked": 9, "nearby_incidents": 4, "cluster_vessels": 3, "note": "n"}})
    assert stored["cluster_vessels"] == 3  # the agent's DBSCAN count wins over incidents + 1


def test_shaping_survives_empty_evidence_and_text_json():
    assert shaping.decode("{bad", {}) == {}
    assert shaping.votes_from({}, 0.1)[0]["fired"] is False
    assert shaping.evidence_lines({})[0]["text"] == "No known jamming area contains the position."


def test_cors_headers_only_for_configured_origins(monkeypatch):
    monkeypatch.setenv("GHAST_CORS_ORIGINS", "http://localhost:3000")
    r = client(FakePool(HEALTH={"position_age": 1.0, "incident_age": 1.0})).options(
        "/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-api-key"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
