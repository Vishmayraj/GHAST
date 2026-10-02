"""GHAST read API and review endpoint.

    cd backend/api
    GHAST_API_KEY=... POSTGRES_DSN=postgresql://... uvicorn main:create_app --factory --port 8000

Every route needs the `X-API-Key` header. The key is a stopgap, real auth is Stage 3, and the app
refuses to start without one. The only write is `POST /incidents/{id}/review`.
"""
from __future__ import annotations

import hmac
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import shaping
from models import (
    HealthOut, IncidentDetail, IncidentPage, IncidentSummary, ReviewIn, ReviewStatsOut, ThresholdsOut, TrackOut,
)
from review_stats import MIN_REVIEWED_TO_TRUST, REVIEWED_QUERY, compute_review_stats

SUMMARY_COLUMNS = """i.id::text AS id, i.mmsi, s.ship_name AS vessel_name, i.flagged_at, i.hypothesis, i.confidence,
       i.status, i.tier, i.priority, i.anomaly_type, i.review_verdict, i.reviewed_by, i.reviewed_at"""

HEALTH_QUERY = """
SELECT EXTRACT(EPOCH FROM now() - (SELECT max(received_at) FROM vessel_position WHERE message_type <> 'historical'))::float AS position_age,
       EXTRACT(EPOCH FROM now() - (SELECT max(flagged_at) FROM incidents))::float AS incident_age
"""

LIST_QUERY = f"""
SELECT {SUMMARY_COLUMNS}
FROM incidents i LEFT JOIN vessel_static s ON s.mmsi = i.mmsi
WHERE ($1::text IS NULL OR i.status = $1)
  AND ($2::text IS NULL OR i.hypothesis = $2)
  AND ($3::bigint IS NULL OR i.mmsi = $3)
  AND ($4::timestamptz IS NULL OR i.flagged_at < $4)
ORDER BY i.flagged_at DESC
LIMIT $5
"""

DETAIL_QUERY = f"""
SELECT {SUMMARY_COLUMNS}, i.window_start, i.window_end, i.anomaly_score, i.evidence, i.tool_call_log,
       i.report_text, i.report_expires_at, i.report_verification, i.challenge, i.review_notes
FROM incidents i LEFT JOIN vessel_static s ON s.mmsi = i.mmsi
WHERE i.id = $1::uuid
"""

# live rows only: the April MarineCadastre import is history, not what the scorer judged
INCIDENT_TRACK_QUERY = """
SELECT received_at, latitude, longitude FROM vessel_position
WHERE mmsi = $1 AND received_at BETWEEN $2 AND $3 AND message_type <> 'historical'
ORDER BY received_at LIMIT 2000
"""

VESSEL_TRACK_QUERY = """
SELECT received_at, latitude, longitude, sog_knots, cog_deg FROM vessel_position
WHERE mmsi = $1 AND received_at BETWEEN $2 AND $3 AND ($4::boolean OR message_type <> 'historical')
ORDER BY received_at LIMIT 5000
"""

ZONES_QUERY = """
SELECT id::text AS id, name, confidence, ST_AsGeoJSON(zone::geometry) AS geometry FROM jamming_zones WHERE active
"""

THRESHOLDS_QUERY = """
SELECT threshold_a, threshold_b, model_version, set_by, reason, created_at FROM threshold_config ORDER BY id DESC LIMIT $1
"""

# The guard keeps a verdict from being overwritten. status 'resolved' means "has a verdict".
REVIEW_QUERY = """
UPDATE incidents
SET review_verdict = $2, reviewed_by = $3, reviewed_at = now(), review_notes = $4, status = 'resolved', updated_at = now()
WHERE id = $1::uuid AND review_verdict IS NULL
RETURNING id::text
"""

EXISTS_QUERY = "SELECT 1 FROM incidents WHERE id = $1::uuid"


def _summary(row: Any) -> IncidentSummary:
    return IncidentSummary(**{k: row[k] for k in IncidentSummary.model_fields})


def create_app(pool: Any = None, api_key: str | None = None) -> FastAPI:
    key = api_key if api_key is not None else os.environ.get("GHAST_API_KEY", "")
    if not key:
        raise RuntimeError("GHAST_API_KEY is not set; the API will not start without a key")
    state: dict[str, Any] = {"pool": pool}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if state["pool"] is None:
            import asyncpg

            state["pool"] = await asyncpg.create_pool(os.environ["POSTGRES_DSN"], min_size=1, max_size=5)
        yield
        if pool is None and state["pool"] is not None:
            await state["pool"].close()

    def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if x_api_key is None or not hmac.compare_digest(x_api_key.encode(), key.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong API key")

    app = FastAPI(title="GHAST API", lifespan=lifespan, dependencies=[Depends(require_key)])
    origins = [o.strip() for o in os.environ.get("GHAST_CORS_ORIGINS", "").split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["X-API-Key", "Content-Type"])

    def db() -> Any:
        return state["pool"]

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        try:
            row = await db().fetchrow(HEALTH_QUERY)
        except Exception as error:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"database unreachable: {type(error).__name__}") from None
        return HealthOut(database="ok", latest_position_age_s=row["position_age"], latest_incident_age_s=row["incident_age"])

    @app.get("/incidents", response_model=IncidentPage)
    async def incidents(
        status: Literal["reported", "escalated", "resolved"] | None = None,
        hypothesis: Literal["jamming", "targeted_spoof", "freeze_replay", "equipment_fault", "benign", "unresolved"] | None = None,
        mmsi: int | None = None,
        limit: int = Query(25, ge=1, le=100),
        before: datetime | None = None,
    ) -> IncidentPage:
        rows = await db().fetch(LIST_QUERY, status, hypothesis, mmsi, before, limit + 1)
        page = rows[:limit]
        # keyset on flagged_at: two incidents with the same flagged_at at a page edge can skip one
        next_before = page[-1]["flagged_at"] if len(rows) > limit and page else None
        return IncidentPage(items=[_summary(r) for r in page], next_before=next_before)

    @app.get("/incidents/{incident_id}", response_model=IncidentDetail)
    async def incident(incident_id: uuid.UUID) -> IncidentDetail:
        row = await db().fetchrow(DETAIL_QUERY, str(incident_id))
        if row is None:
            raise HTTPException(status_code=404, detail="incident not found")
        evidence = shaping.decode(row["evidence"], {})
        log = shaping.decode(row["tool_call_log"], [])
        start = row["window_start"] or row["flagged_at"] - timedelta(hours=2)
        track = await db().fetch(INCIDENT_TRACK_QUERY, row["mmsi"], start, row["flagged_at"])
        return IncidentDetail(
            **_summary(row).model_dump(),
            window_start=row["window_start"], window_end=row["window_end"], anomaly_score=row["anomaly_score"],
            votes=shaping.votes_from(evidence, row["anomaly_score"]), evidence=shaping.evidence_lines(evidence),
            fleet_context=shaping.fleet_context_from(evidence), challenge=shaping.challenge_from(row["challenge"]),
            tool_call_log=[shaping.log_summary(e) for e in log if isinstance(e, dict)],
            report_text=row["report_text"], report_expires_at=row["report_expires_at"],
            report_verification=shaping.decode(row["report_verification"], None),
            review_notes=row["review_notes"], track=shaping.track_points(track, row["flagged_at"]),
        )

    @app.get("/vessels/{mmsi}/track", response_model=TrackOut)
    async def vessel_track(mmsi: int, from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
                           include_historical: bool = False) -> TrackOut:
        end = to or datetime.now(timezone.utc)
        rows = await db().fetch(VESSEL_TRACK_QUERY, mmsi, from_ or end - timedelta(hours=24), end, include_historical)
        return TrackOut(mmsi=mmsi, points=[{"time": r["received_at"], "lat": r["latitude"], "lon": r["longitude"],
                                            "sog_knots": r["sog_knots"], "cog_deg": r["cog_deg"]} for r in rows])

    @app.get("/vessels/{mmsi}/incidents", response_model=IncidentPage)
    async def vessel_incidents(mmsi: int, limit: int = Query(25, ge=1, le=100)) -> IncidentPage:
        rows = await db().fetch(LIST_QUERY, None, None, mmsi, None, limit)
        return IncidentPage(items=[_summary(r) for r in rows], next_before=None)

    @app.get("/zones")
    async def zones() -> dict[str, Any]:
        import json

        rows = await db().fetch(ZONES_QUERY)
        features = [{"type": "Feature", "id": r["id"], "properties": {"name": r["name"], "confidence": r["confidence"]},
                     "geometry": json.loads(r["geometry"])} for r in rows]
        return {"type": "FeatureCollection", "features": features}

    @app.get("/review-stats", response_model=ReviewStatsOut)
    async def review_stats() -> dict[str, Any]:
        rows = [dict(r) for r in await db().fetch(REVIEWED_QUERY)]
        return shaping.review_stats_payload(compute_review_stats(rows), MIN_REVIEWED_TO_TRUST)

    @app.get("/thresholds", response_model=ThresholdsOut)
    async def thresholds(limit: int = Query(10, ge=1, le=50)) -> ThresholdsOut:
        rows = [dict(r) for r in await db().fetch(THRESHOLDS_QUERY, limit)]
        return ThresholdsOut(active=rows[0] if rows else None, history=rows[1:])

    @app.post("/incidents/{incident_id}/review", response_model=IncidentSummary)
    async def review(incident_id: uuid.UUID, body: ReviewIn) -> IncidentSummary:
        done = await db().fetchrow(REVIEW_QUERY, str(incident_id), body.verdict, body.reviewer, body.notes)
        if done is None:
            if await db().fetchrow(EXISTS_QUERY, str(incident_id)) is None:
                raise HTTPException(status_code=404, detail="incident not found")
            raise HTTPException(status_code=409, detail="incident already has a verdict")
        row = await db().fetchrow(DETAIL_QUERY, str(incident_id))
        return _summary(row)

    return app
