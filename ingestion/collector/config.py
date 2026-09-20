"""Stage 1 ingestion configuration, read from environment variables.

All AIS Stream connection details and storage targets live here so
main.py and infra/docker/docker-compose.yml stay in sync with
infra/docker/.env.example.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# Whole-planet bounding box. AIS Stream's own docs recommend keeping this
# focused on your application; override with AISSTREAM_BOUNDING_BOXES once
# we know which region(s) Stage 1 actually wants to watch.
DEFAULT_BOUNDING_BOXES = [[[-90.0, -180.0], [90.0, 180.0]]]

DEFAULT_MESSAGE_TYPES = [
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "ShipStaticData",
]


def _bounding_boxes_from_env() -> list:
    raw = os.environ.get("AISSTREAM_BOUNDING_BOXES")
    if not raw:
        return DEFAULT_BOUNDING_BOXES
    return json.loads(raw)


def _list_from_env(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class IngestionConfig:
    aisstream_api_key: str
    bounding_boxes: list = field(default_factory=lambda: DEFAULT_BOUNDING_BOXES)
    filter_mmsi: list[str] = field(default_factory=list)
    filter_message_types: list[str] = field(default_factory=lambda: list(DEFAULT_MESSAGE_TYPES))
    postgres_dsn: str = "postgresql://ghast:ghast@localhost:5432/ghast"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "ghast"
    minio_secret_key: str = "ghast12345"
    minio_bucket: str = "ghast-raw-ais"
    minio_secure: bool = False

    @classmethod
    def from_env(cls) -> "IngestionConfig":
        api_key = os.environ.get("AISSTREAM_API_KEY")
        if not api_key:
            raise RuntimeError(
                "AISSTREAM_API_KEY is not set. Get a free key at https://aisstream.io "
                "and put it in infra/docker/.env (see infra/docker/README.md)."
            )
        return cls(
            aisstream_api_key=api_key,
            bounding_boxes=_bounding_boxes_from_env(),
            filter_mmsi=_list_from_env("AISSTREAM_FILTER_MMSI"),
            filter_message_types=_list_from_env(
                "AISSTREAM_FILTER_MESSAGE_TYPES", DEFAULT_MESSAGE_TYPES
            ),
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN", "postgresql://ghast:ghast@timescaledb:5432/ghast"
            ),
            minio_endpoint=os.environ.get("MINIO_ENDPOINT", "minio:9000"),
            minio_access_key=os.environ.get("MINIO_ROOT_USER", "ghast"),
            minio_secret_key=os.environ.get("MINIO_ROOT_PASSWORD", "ghast12345"),
            minio_bucket=os.environ.get("MINIO_RAW_BUCKET", "ghast-raw-ais"),
            minio_secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
        )
