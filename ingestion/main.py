"""Stage 1 ingestion entrypoint: AIS Stream -> normalize -> TimescaleDB,
with the raw envelope archived to MinIO alongside.

Run locally:
    cd ingestion
    pip install -r requirements.txt
    export AISSTREAM_API_KEY=...   # see infra/docker/.env.example for the rest
    python main.py

Run via Docker: infra/docker/docker-compose.yml's "ingestion" service.
"""

from __future__ import annotations

import asyncio
import logging
import time

from collector.client import stream_envelopes
from collector.config import IngestionConfig
from normalizer.normalize import normalize_envelope
from storage import RawArchiver, TimescaleWriter

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("ghast.ingestion")

# Batch writes so a busy stream doesn't issue one DB round-trip per message,
# but flush on a timer too so a quiet bounding box still lands data promptly.
_POSITION_BATCH_SIZE = 200
_STATIC_BATCH_SIZE = 50
_FLUSH_INTERVAL_SECONDS = 10.0


async def run() -> None:
    config = IngestionConfig.from_env()

    archiver = RawArchiver(config)
    archiver.ensure_bucket()

    writer = TimescaleWriter(config.postgres_dsn)
    await writer.connect()

    position_batch: list[dict] = []
    static_batch: list[dict] = []
    last_flush = time.monotonic()

    async def flush_all() -> None:
        nonlocal position_batch, static_batch, last_flush
        if position_batch:
            await writer.write_positions(position_batch)
            position_batch = []
        if static_batch:
            await writer.write_static(static_batch)
            static_batch = []
        archiver.flush()
        last_flush = time.monotonic()

    try:
        async for envelope in stream_envelopes(config):
            archiver.add(envelope)

            record = normalize_envelope(envelope)
            if record is not None:
                if record["kind"] == "position":
                    position_batch.append(record)
                    if len(position_batch) >= _POSITION_BATCH_SIZE:
                        await writer.write_positions(position_batch)
                        position_batch = []
                elif record["kind"] == "static":
                    static_batch.append(record)
                    if len(static_batch) >= _STATIC_BATCH_SIZE:
                        await writer.write_static(static_batch)
                        static_batch = []

            if time.monotonic() - last_flush >= _FLUSH_INTERVAL_SECONDS:
                await flush_all()
    finally:
        await flush_all()
        await writer.close()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Shutting down ingestion service")
