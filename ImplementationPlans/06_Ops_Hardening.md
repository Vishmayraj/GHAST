# 06 Ops hardening: reproducible, safe to restart, tested against SQL

Depends on: nothing. Parts can run alongside 01.

## Why

A fresh clone cannot run the scorer (no checkpoint, no Laya weights), the schema has no migrations, the importer is not idempotent, ingestion dies on one bad timestamp, and no test runs SQL. `docs/architecture.md` known problems 12 to 15 and `docs/testing.md` "Gaps that matter" are the source list.

## Steps

1. Artifacts. Decide the store with the owner (MinIO bucket, GitHub release assets, other). Upload `epoch_010.pt`, later checkpoints and `model.safetensors`. Add `scripts/fetch_artifacts.py` with a `artifacts.json` manifest of name, URL, sha256, and verify checksums on fetch. Make the scorer fail with a clear message pointing at the script when the file is missing. Make `GHAST_CHECKPOINT` come from `.env` in compose instead of a hardcoded `epoch_010.pt`.
2. Migrations. Numbered SQL files in `backend/models/migrations/` plus a `schema_migrations` table and a tiny runner (`scripts/migrate.py`). `schema.sql` becomes the result of applying all. Ingestion stops applying the whole file on every start and instead runs the migrator.
3. Dedupe. Add a unique index that includes the hypertable time column (for example `(mmsi, received_at, latitude, longitude)`) and switch the insert to `ON CONFLICT DO NOTHING`. Migration must handle existing duplicates first.
4. Idempotent importer. Record loaded files in a table (`imported_files`, name and sha256) and skip them. Test with a fake connection.
5. Normalizer hygiene. Range-check latitude and longitude, honor `Valid`, translate AIS not-available sentinels (heading 511, COG 360, SOG 102.3) to null, and make a malformed timestamp drop one message with a log line instead of ending the process. Tests for each. (Model-side sentinel handling is plan 05; keep the column contract the same.)
6. Healthchecks in compose: `pg_isready` for timescaledb, an HTTP check for MinIO, a heartbeat file or endpoint for ingestion and scoring; `depends_on: condition: service_healthy`. Remove the meaningless `frontend depends_on ingestion`.
7. SQL tests in CI. Add a workflow with a `timescale/timescaledb-ha:pg16` service container. Tests apply migrations and exercise the real queries: `POSITION_QUERY` and friends, `RECENT_REPORTS_QUERY`, `ACTIVE_VESSELS_QUERY`, `OPEN_INCIDENT_QUERY`, `JAMMING_ZONE_QUERY`, `TRACK_HISTORY_QUERY`, `find_similar_incidents`, `persist_incident`. Seed the test database with a handful of hand-written rows; that is fixture data for a query test, not a data source.
8. CI path filters. `agent-tests` should also trigger on `ml/features/**`; `ml-evaluation-tests` on `ml/training/**`. Pin CPU torch in CI to shrink downloads.
9. Archive replay. A small tool that reads the MinIO ndjson archive and re-runs it through the normalizer, so the write-only archive can rebuild `vessel_position`. Test the read side with a fake client.
10. Secrets and defaults. Move the MinIO and Postgres defaults out of compose defaults into `.env.example` with a note that they are dev-only.

## Done when

- A fresh clone plus `fetch_artifacts.py` plus `docker compose up` runs the scorer.
- Migrations apply cleanly to an empty database and to the current one.
- The SQL workflow is green and covers the queries listed.
- `docs/infrastructure.md`, `docs/data-pipeline.md`, `docs/testing.md` are updated from the code.

## Not run without services

Steps 1, 3 and 7 need real infrastructure. State which steps were only written and unit-tested.
