# backend/models/

Database schema for tracks, incidents, and jamming zones, backed by TimescaleDB.

`schema.sql` is the current Stage 1 schema (`vessel_position` hypertable + `vessel_static`), applied automatically by `ingestion/storage.py` on startup. No migration tool yet - Alembic or similar is a Stage 2 concern once the schema needs to evolve under real data rather than being redefined wholesale.
