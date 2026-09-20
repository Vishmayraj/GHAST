# ingestion/

Pulls real AIS data continuously from a public source and turns it into clean, normalized records in TimescaleDB. This is Weeks 1-4 of `ImplementationPlans/Sem5IP.md`.

- `collector/` — the AIS Stream (aisstream.io) websocket client and config.
- `normalizer/` — turns a raw AIS Stream envelope into GHAST's internal record shapes.
- `storage.py` — writes normalized records into TimescaleDB and archives raw envelopes to MinIO.
- `main.py` — the service entrypoint wiring the three together.

**Data source note:** the HLD's tech stack table (section 6) names `pyais` for AIVDM/NMEA decoding, written before a specific provider was chosen. We're using AIS Stream (aisstream.io), which delivers already-decoded JSON over a websocket, so there's no raw NMEA to decode - `normalizer/` validates and reshapes AIS Stream's JSON instead. `pyais` would come back into play if a second, NMEA-level data source gets added later.

Run locally: `cd ingestion && pip install -r requirements.txt`, set `AISSTREAM_API_KEY` and the Postgres/MinIO env vars (see `infra/docker/.env.example`), then `python main.py`. Or just `docker compose up -d` from `infra/docker/` - see that directory's README.
