"""Storage writers for the ingestion pipeline.

TimescaleWriter lands normalized position/static records in Postgres.
RawArchiver batches raw envelopes and flushes them to MinIO as
newline-delimited JSON, matching the "archived raw AIS messages, points
to object storage" role data/raw/ documents.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
from minio import Minio

from collector.config import IngestionConfig

logger = logging.getLogger(__name__)

# backend/models/ owns the DB schema per the repo layout (ImplementationPlans/Sem5IP.md
# section 6); this ingestion service just applies it on startup.
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "backend" / "models" / "schema.sql"

_INSERT_POSITION_SQL = """
    INSERT INTO vessel_position (
        received_at, mmsi, ship_name, message_type,
        latitude, longitude, position,
        sog_knots, cog_deg, true_heading_deg,
        rate_of_turn, navigational_status, raim, position_accuracy
    ) VALUES (
        $1, $2, $3, $4,
        $5, $6, ST_SetSRID(ST_MakePoint($6, $5), 4326)::geography,
        $7, $8, $9, $10, $11, $12, $13
    )
"""

_UPSERT_STATIC_SQL = """
    INSERT INTO vessel_static (
        mmsi, ship_name, call_sign, imo_number, ship_type, destination, max_draught, updated_at
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, now())
    ON CONFLICT (mmsi) DO UPDATE SET
        ship_name = EXCLUDED.ship_name,
        call_sign = EXCLUDED.call_sign,
        imo_number = EXCLUDED.imo_number,
        ship_type = EXCLUDED.ship_type,
        destination = EXCLUDED.destination,
        max_draught = EXCLUDED.max_draught,
        updated_at = now()
"""


class TimescaleWriter:
    """Batched writer into TimescaleDB for normalized position/static records."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        if _SCHEMA_PATH.exists():
            async with self._pool.acquire() as conn:
                await conn.execute(_SCHEMA_PATH.read_text())
        else:
            logger.warning(
                "Schema file not found at %s; assuming tables already exist", _SCHEMA_PATH
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    async def write_positions(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        assert self._pool is not None, "call connect() before writing"
        params = [
            (
                r["received_at"],
                r["mmsi"],
                r["ship_name"],
                r["message_type"],
                r["latitude"],
                r["longitude"],
                r.get("sog_knots"),
                r.get("cog_deg"),
                r.get("true_heading_deg"),
                r.get("rate_of_turn"),
                r.get("navigational_status"),
                r.get("raim"),
                r.get("position_accuracy"),
            )
            for r in records
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(_INSERT_POSITION_SQL, params)
        logger.info("Wrote %d position record(s) to vessel_position", len(records))

    async def write_static(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        assert self._pool is not None, "call connect() before writing"
        params = [
            (
                r["mmsi"],
                r["ship_name"],
                r.get("call_sign"),
                r.get("imo_number"),
                r.get("ship_type"),
                r.get("destination"),
                r.get("max_draught"),
            )
            for r in records
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(_UPSERT_STATIC_SQL, params)
        logger.info("Upserted %d static record(s) into vessel_static", len(records))


class RawArchiver:
    """Buffers raw AIS Stream envelopes and periodically flushes them as
    newline-delimited JSON objects to MinIO - the object storage backing
    data/raw/ (see data/raw/README.md)."""

    def __init__(self, config: IngestionConfig, flush_every: int = 500):
        self._client = Minio(
            config.minio_endpoint,
            access_key=config.minio_access_key,
            secret_key=config.minio_secret_key,
            secure=config.minio_secure,
        )
        self._bucket = config.minio_bucket
        self._flush_every = flush_every
        self._buffer: list[str] = []

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
            logger.info("Created MinIO bucket %s", self._bucket)

    def add(self, envelope: dict[str, Any]) -> None:
        self._buffer.append(json.dumps(envelope, separators=(",", ":")))
        if len(self._buffer) >= self._flush_every:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        payload = ("\n".join(self._buffer) + "\n").encode("utf-8")
        now = datetime.now(timezone.utc)
        key = f"ais-envelopes/{now:%Y/%m/%d}/{now:%Y%m%dT%H%M%S%f}.ndjson"
        self._client.put_object(self._bucket, key, io.BytesIO(payload), length=len(payload))
        logger.info("Archived %d raw envelope(s) to %s/%s", len(self._buffer), self._bucket, key)
        self._buffer.clear()
