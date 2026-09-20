# ingestion/collector/

The AIS Stream client: connects to `wss://stream.aisstream.io/v0/stream`, sends the required subscription within 3 seconds of connecting, and yields decoded envelopes forever, reconnecting with exponential backoff + jitter on any drop (AIS Stream gives no uptime SLA, so this is expected to happen).

- `config.py` — `IngestionConfig`, built from environment variables (API key, bounding boxes, MMSI/message-type filters, storage targets). See `infra/docker/.env.example` for every variable it reads.
- `client.py` — `stream_envelopes()`, an async generator over decoded AIS Stream JSON envelopes.

Bounding boxes default to the whole planet; narrow `AISSTREAM_BOUNDING_BOXES` once we've picked a region/strait to focus the Stage 1 demo on (AIS Stream's own docs recommend this, both for signal quality and because free-tier bandwidth is shared).
