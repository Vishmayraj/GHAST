"""AIS Stream WebSocket client.

Connects to wss://stream.aisstream.io/v0/stream, sends the subscription
within the 3-second window AIS Stream requires, and yields decoded
envelopes forever. AIS Stream gives no delivery guarantee or uptime SLA
(see https://aisstream.io/documentation) and expects clients to
reconnect themselves with backoff, which is what this does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator

import websockets

from .config import IngestionConfig

logger = logging.getLogger(__name__)

STREAM_URL = "wss://stream.aisstream.io/v0/stream"

# A dropped connection is a normal, expected event on this service, not an
# error path - back off, then try again.
_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0


def build_subscription_message(config: IngestionConfig) -> str:
    """Build the JSON subscription AIS Stream expects within 3s of connect.

    Resending a subscription on the same connection *replaces* it rather
    than merging, so this is also what a future "update the watch area at
    runtime" feature would resend.
    """
    message: dict = {
        "APIKey": config.aisstream_api_key,
        "BoundingBoxes": config.bounding_boxes,
    }
    if config.filter_mmsi:
        message["FiltersShipMMSI"] = config.filter_mmsi
    if config.filter_message_types:
        message["FilterMessageTypes"] = config.filter_message_types
    return json.dumps(message)


async def stream_envelopes(config: IngestionConfig) -> AsyncIterator[dict]:
    """Yield decoded AIS Stream envelopes forever, reconnecting on drop."""
    backoff = _INITIAL_BACKOFF_SECONDS
    while True:
        try:
            async with websockets.connect(STREAM_URL, compression="deflate") as ws:
                await ws.send(build_subscription_message(config))
                logger.info(
                    "Subscribed to AIS Stream (%d bounding box(es), %d message type(s))",
                    len(config.bounding_boxes),
                    len(config.filter_message_types),
                )
                backoff = _INITIAL_BACKOFF_SECONDS  # reset after a clean connect
                async for raw in ws:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("Dropped a non-JSON frame from AIS Stream")
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            jitter = random.uniform(0, backoff * 0.25)
            wait_for = min(backoff, _MAX_BACKOFF_SECONDS) + jitter
            logger.warning(
                "AIS Stream connection dropped (%s); reconnecting in %.1fs", exc, wait_for
            )
            await asyncio.sleep(wait_for)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
