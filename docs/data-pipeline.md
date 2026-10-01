# Data pipeline

How AIS data gets into PostgreSQL/TimescaleDB, what the tables look like, and how live and historical rows are told apart. Everything here is read from the code in this checkout. Where a statement is an inference rather than something the code enforces or a test covers, it says so.

Position data reaches `vessel_position` by two unrelated routes:

```text
wss://stream.aisstream.io  ->  ingestion/  ->  vessel_position, vessel_static   (live)
                                           ->  MinIO ndjson archive             (live, write-only)

data/raw/ais-2026-04-*.csv ->  scripts/import_marinecadastre.py -> vessel_position   (historical)
```

Everything downstream (`ml/`, `scoring/`, `agent/`) reads from the tables, never from the feed or the CSV files.

## Live vs historical rows

There is no `source` column. The only marker is `vessel_position.message_type`.

| | Live (AISStream) | Historical (MarineCadastre) |
|---|---|---|
| `message_type` | AISStream message name: `PositionReport`, `StandardClassBPositionReport`, `ExtendedClassBPositionReport` | the literal string `historical` |
| `received_at` | `MetaData.time_utc` from the envelope (receiver-side timestamp), or `now()` if absent | `base_date_time` from the CSV |
| `rate_of_turn` | from the message | always NULL (not in the CSV) |
| `raim`, `position_accuracy` | from the message | always NULL |
| `true_heading_deg`, `navigational_status`, `sog_knots`, `cog_deg` | from the message | from `heading`, `status`, `sog`, `cog` |
| `vessel_static` row | written by the same service from `ShipStaticData` | not written |
| region | whatever `AISSTREAM_BOUNDING_BOXES` allows (default: whole planet) | lat 25 to 42, lon -82 to -65 (hardcoded in the importer) |

Downstream code separates the two with a predicate on `message_type`:

- `message_type IS DISTINCT FROM 'historical'` means live. Used by `ml/features/pipeline.py` (`POSITION_QUERY`, `COUNT_QUERY`, `LIVE_COVERAGE_QUERY`) and `scoring/live_scorer.py` (`ACTIVE_VESSELS_QUERY`, `RECENT_REPORTS_QUERY`).
- `message_type = 'historical'` means historical. Used by `HISTORICAL_POSITION_QUERY` and `HISTORICAL_COUNT_QUERY` in `ml/features/pipeline.py`.
- The agent tools do not filter. `agent/tools/track_history.py` reads all rows for an MMSI in the 24 hours up to the flagged time regardless of source (reports after the flag only when a caller asks for them), and `agent/tools/incident_history.py` never touches `vessel_position`.

Consequence of the "live is anything that is not historical" rule: a future third source would be treated as live unless it also sets `message_type = 'historical'`.

Another consequence, worth knowing before comparing models trained on one source and scored on the other: the two sources do not carry the same columns (table above), and `received_at` means different things in each. The BiLSTM feature set uses `rate_of_turn`, so historical-trained models have only ever seen it as missing. See `docs/ml-pipeline.md`.

## Live ingestion

Files: `ingestion/main.py`, `ingestion/collector/`, `ingestion/normalizer/normalize.py`, `ingestion/storage.py`.

`main.run()` reads `IngestionConfig.from_env()`, creates a `RawArchiver` and a `TimescaleWriter`, then iterates `stream_envelopes()` forever.

Collector (`collector/client.py`):

- Connects to `wss://stream.aisstream.io/v0/stream` with `compression="deflate"` and sends one subscription JSON immediately: `APIKey`, `BoundingBoxes`, optional `FiltersShipMMSI`, optional `FilterMessageTypes`.
- Yields each frame as a decoded dict. A frame that is not valid JSON is logged and dropped.
- On `WebSocketException` or `OSError` it sleeps with exponential backoff (1 s doubling to 60 s, plus up to 25% jitter) and reconnects. Backoff resets after a successful connect. Any other exception propagates and ends the process.

Config (`collector/config.py`): the dataclass defaults point at `localhost`, but `from_env()` supplies different defaults (`timescaledb:5432`, `minio:9000`) because it is written for the compose network. Running `python main.py` on the host without `POSTGRES_DSN` and `MINIO_ENDPOINT` set therefore tries the container hostnames.

Message types requested by default: `PositionReport`, `StandardClassBPositionReport`, `ExtendedClassBPositionReport`, `ShipStaticData`. The normalizer handles exactly these four and returns `None` for anything else, including `SubscriptionConfirmation`.

Normalizer (`normalize_envelope`, pure function):

- Position types produce `{"kind": "position", ...}` with `mmsi`, `received_at`, `ship_name`, `message_type`, `latitude`, `longitude`, `sog_knots` (`Sog`), `cog_deg` (`Cog`), `true_heading_deg` (`TrueHeading`), `rate_of_turn` (`RateOfTurn`), `navigational_status`, `raim`, `position_accuracy`.
- `ShipStaticData` produces `{"kind": "static", ...}` with `call_sign`, `imo_number`, `ship_type` (`Type`), `destination`, `max_draught` (`MaximumStaticDraught`). `ship_name` comes from `MetaData.ShipName`, not from the body's `Name`.
- Metadata keys are looked up in both casings (`MMSI`/`mmsi`, `Latitude`/`latitude`, `time_utc`/`TimeUtc`).
- `time_utc` looks like `2023-05-10 11:46:52.509865357 +0000 UTC`; nanoseconds are truncated to microseconds. An unparseable string raises `ValueError`, which is not caught in `main.py` and would end the service.
- Not done: no range check on latitude/longitude, no check of the `Valid` flag, and AIS "not available" sentinels (for example heading 511) are stored as reported.

Batching and writes (`main.py`, `storage.py`):

| Constant | Value | Where |
|---|---|---|
| position batch size | 200 | `_POSITION_BATCH_SIZE` |
| static batch size | 50 | `_STATIC_BATCH_SIZE` |
| flush interval | 10 s | `_FLUSH_INTERVAL_SECONDS` |
| raw archive flush size | 500 envelopes | `RawArchiver(flush_every=500)` |

- Positions are written with `executemany` of an `INSERT` that builds the geography with `ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography`. There is no unique constraint and no `ON CONFLICT`, so a repeated message is stored twice.
- Static records are upserted on `mmsi` and always overwrite every column (`vessel_static` keeps no history, only the latest message).
- The 10 second timer is checked only when an envelope arrives. On a stream that goes quiet, a partial batch waits for the next message.
- The asyncpg pool has `min_size=1, max_size=5`.
- `TimescaleWriter.connect()` runs the whole of `backend/models/schema.sql` on every start. That file is the only place tables are created. If the file is missing the writer logs a warning and assumes the tables exist.
- A failed database write raises out of `run()`. The `finally` block tries to flush once more and closes the pool; buffered rows from that batch are lost if the write keeps failing. Compose restarts the container (`restart: unless-stopped`).

## Raw archive (MinIO)

`RawArchiver` buffers every envelope (including ones the normalizer ignores) as compact JSON lines and writes them to MinIO as one object per flush:

```text
bucket: ghast-raw-ais            (MINIO_RAW_BUCKET)
key:    ais-envelopes/YYYY/MM/DD/YYYYMMDDTHHMMSSffffff.ndjson
```

The bucket is created on startup if it does not exist. Nothing in the repository reads the archive back: there is no replay tool, no backfill from it, and no test. It is a write-only log at present. The MinIO client calls are synchronous and run inside the asyncio loop, so each flush blocks the loop for the duration of the upload.

`data/raw/` on disk is gitignored and, in this checkout, holds only a README. The historical CSV files are expected there (below), which is unrelated to the MinIO archive despite the shared name.

## Historical import

```text
python scripts/import_marinecadastre.py
```

Run from the repository root. Behavior, from `scripts/import_marinecadastre.py`:

- Reads `data/raw/ais-2026-04-*.csv` (the glob is hardcoded to April 2026), in sorted order.
- Connects with `POSTGRES_DSN`, default `postgresql://ghast:ghast@localhost:5432/ghast`.
- Requires the columns `mmsi, base_date_time, longitude, latitude, sog, cog, heading, vessel_name, imo, call_sign, vessel_type, status, length, width, draft, cargo, transceiver` and fails if any is missing.
- Reads 100,000 rows per pandas chunk, coerces numerics, parses timestamps as UTC, drops rows with a missing MMSI, time, latitude or longitude, then keeps rows inside lat 25 to 42, lon -82 to -65.
- Writes with `COPY ... FROM STDIN (FORMAT CSV)` into `vessel_position`, one commit per chunk. `position` is written as an EWKT string `SRID=4326;POINT(lon lat)`.
- Column mapping: `vessel_name` to `ship_name`, `sog` to `sog_knots`, `cog` to `cog_deg`, `heading` to `true_heading_deg`, `status` to `navigational_status`. `message_type` is set to `historical`. `rate_of_turn`, `raim`, `position_accuracy` are NULL.
- The columns `imo`, `call_sign`, `vessel_type`, `length`, `width`, `draft`, `cargo`, `transceiver` are validated as present and then discarded. Nothing is written to `vessel_static`.

Things that are not obvious from the command:

- The table must already exist. `verify_database()` runs `SELECT ... FROM vessel_position` first, and nothing in the importer creates tables. The schema is applied by ingestion on startup, so either start the compose `ingestion` service once or apply `backend/models/schema.sql` by hand before importing.
- It is not idempotent. There is no dedupe and no record of which files were loaded, so running it twice doubles the rows. A failure partway leaves the chunks already committed in place.
- Dependencies (`pandas`, `psycopg`) are not listed in a requirements file of their own. They are both in `ml/requirements.txt`.
- The script prints its progress with `print`, not `logging`, and has no tests.

The repo's own docs (`ml/training/README.md`, `docs/DEVELOPER_GUIDE.md`) describe the imported set as roughly 31 million rows for April 1 to about April 15/16, 2026. The importer does not enforce that end date; it loads whatever `ais-2026-04-*.csv` files are present.

## Database schema

Defined in `backend/models/schema.sql`. Requires the `timescaledb` and `postgis` extensions (`timescale/timescaledb-ha:pg16` ships both). Every statement is `IF NOT EXISTS` except the `incidents.hypothesis` constraint, which is dropped and re-added on every run. There is no migration tool; changing a column means editing SQL by hand on an existing database.

### `vessel_position` (hypertable on `received_at`)

| Column | Type | Notes |
|---|---|---|
| `received_at` | TIMESTAMPTZ NOT NULL | hypertable time dimension |
| `mmsi` | BIGINT NOT NULL | |
| `ship_name` | TEXT | |
| `message_type` | TEXT NOT NULL | see "Live vs historical rows" |
| `latitude`, `longitude` | DOUBLE PRECISION NOT NULL | the ML code reads these two, not `position` |
| `position` | GEOGRAPHY(POINT, 4326) NOT NULL | duplicates lat/lon, used by spatial queries |
| `sog_knots`, `cog_deg` | DOUBLE PRECISION | |
| `true_heading_deg`, `rate_of_turn`, `navigational_status` | INTEGER | |
| `raim`, `position_accuracy` | BOOLEAN | |

Indexes: `vessel_position_mmsi_time_idx (mmsi, received_at DESC)` and `vessel_position_geo_idx` (GiST on `position`), plus the default `received_at` index that `create_hypertable` adds. There is no index on `message_type`, and no primary or unique key. Chunk interval, compression and retention are not configured, so TimescaleDB defaults apply.

### `vessel_static`

`mmsi BIGINT PRIMARY KEY`, `ship_name`, `call_sign`, `imo_number BIGINT`, `ship_type INTEGER`, `destination`, `max_draught DOUBLE PRECISION`, `updated_at TIMESTAMPTZ DEFAULT now()`. Written only by live ingestion. The ML feature query left-joins on it to get `ship_type` (the "vessel class" model input); a vessel never seen in a live `ShipStaticData` message has no row.

### `incidents`

One row per investigation, written by `agent/orchestrator/state_machine.py::persist_incident`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `mmsi` | BIGINT NOT NULL | |
| `flagged_at` | TIMESTAMPTZ NOT NULL | timestamp of the flagged report |
| `window_start`, `window_end` | TIMESTAMPTZ | first and last report time of the scored window, written by `persist_incident`; NULL on rows stored before this was added |
| `flagged_position` | GEOGRAPHY(POINT, 4326) | |
| `anomaly_score` | DOUBLE PRECISION NOT NULL | the BiLSTM prediction error at the flagged report |
| `anomaly_type` | TEXT | the sorted detector votes joined with `+`, e.g. `freeze_replay+prediction_error` |
| `hypothesis` | TEXT NOT NULL | CHECK in `jamming, targeted_spoof, freeze_replay, equipment_fault, benign, unresolved` |
| `confidence` | DOUBLE PRECISION | |
| `status` | TEXT NOT NULL | CHECK in `reported, escalated, resolved`; the agent writes the first two, `agent/review.py` sets `resolved` when it records a verdict |
| `evidence`, `tool_call_log` | JSONB | full tool outputs and the ordered call log |
| `report_text` | TEXT | NULL until an analyst requests a report and it succeeds (`agent/report_generator/on_demand.py`) |
| `report_generated_at` | TIMESTAMPTZ | when `report_text` was stored |
| `review_verdict` | TEXT | analyst verdict, CHECK in `confirmed_spoof, jamming, equipment_fault, benign, unclear`; NULL until reviewed |
| `reviewed_by`, `reviewed_at`, `review_notes` | TEXT, TIMESTAMPTZ, TEXT | set with the verdict by `agent/review.py` |
| `created_at`, `updated_at` | TIMESTAMPTZ | `updated_at` has no trigger; the review CLI sets it when it records a verdict |

Indexes: `(mmsi, flagged_at DESC)`, `status`, GiST on `flagged_position`.

The `ALTER TABLE ... DROP CONSTRAINT / ADD CONSTRAINT` block exists because `freeze_replay` was added to the allowed hypotheses after the table's first definition (commit `8b50601`), and `CREATE TABLE IF NOT EXISTS` does not change an existing table.

### `jamming_zones`

`id`, `name`, `zone GEOGRAPHY(MULTIPOLYGON, 4326)`, `source` (`manual` or `automated`), `confidence`, `active`, `first_seen`, `last_seen`, `notes`, timestamps; GiST index on `zone`, index on `active`. Nothing in the repository inserts into this table. `data/jamming_zones/` holds a README and no data. The agent's `jamming_zones` tool therefore returns `matched: false` on a fresh database, and the `jamming` hypothesis cannot fire until rows are inserted by hand.

## Preprocessing that happens after the tables

The pipeline does not normalize data inside the database. Feature extraction and window construction are in `ml/features/` and are described in `docs/ml-pipeline.md`. The only preprocessing on the way in is the normalizer's reshaping and the importer's coercion and region filter described above.

## Status

| Piece | State |
|---|---|
| live ingestion (collector, normalizer, writer) | implemented, used; only `normalize_envelope` has tests (8) |
| collector reconnect, config parsing, storage, MinIO archiver | implemented, no tests |
| raw archive read-back / replay | not implemented |
| historical importer | implemented, used for the April 2026 backfill; no tests, not idempotent |
| schema | implemented; no migrations |
| `jamming_zones` data | table exists, no data source, no loader |
| `vessel_static` for historical vessels | not populated by the importer |

Known limitations, all read from code and not measured:

- No coordinate range check or AIS sentinel handling in the normalizer.
- No dedupe on `vessel_position`; the importer can double-load.
- A malformed `time_utc` or a database error stops the ingestion process.
- `IngestionConfig` has two different sets of host defaults depending on how it is constructed.
