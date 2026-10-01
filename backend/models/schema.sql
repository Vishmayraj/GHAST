-- Stage 1 core tables: vessel positions (hypertable), latest static data,
-- investigation incidents, and known jamming/spoofing zones.
-- Requires the TimescaleDB and PostGIS extensions - both are bundled in
-- the timescale/timescaledb-ha image used by infra/docker/docker-compose.yml.
--
-- Applied automatically by ingestion/storage.py on startup (CREATE ... IF
-- NOT EXISTS throughout, so it's safe to run every time). No migration
-- tool yet at Stage 1 - Alembic or similar is a Stage 2 concern once the
-- schema needs to evolve under real data.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;

-- One row per AIS position report received. This is the hypertable the
-- ML detection layer (ml/) reads trajectories from and the agent's
-- track_history tool (agent/tools/) queries.
CREATE TABLE IF NOT EXISTS vessel_position (
    received_at          TIMESTAMPTZ NOT NULL,
    mmsi                 BIGINT NOT NULL,
    ship_name            TEXT,
    message_type         TEXT NOT NULL,
    latitude             DOUBLE PRECISION NOT NULL,
    longitude            DOUBLE PRECISION NOT NULL,
    position             GEOGRAPHY(POINT, 4326) NOT NULL,
    sog_knots            DOUBLE PRECISION,
    cog_deg              DOUBLE PRECISION,
    true_heading_deg     INTEGER,
    rate_of_turn         INTEGER,
    navigational_status  INTEGER,
    raim                 BOOLEAN,
    position_accuracy    BOOLEAN
);

SELECT create_hypertable('vessel_position', 'received_at', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS vessel_position_mmsi_time_idx
    ON vessel_position (mmsi, received_at DESC);
CREATE INDEX IF NOT EXISTS vessel_position_geo_idx
    ON vessel_position USING GIST (position);

-- One row per known vessel, kept up to date as ShipStaticData messages
-- arrive. Used for vessel-identity lookups (name, type, destination)
-- rather than trajectory analysis.
CREATE TABLE IF NOT EXISTS vessel_static (
    mmsi         BIGINT PRIMARY KEY,
    ship_name    TEXT,
    call_sign    TEXT,
    imo_number   BIGINT,
    ship_type    INTEGER,
    destination  TEXT,
    max_draught  DOUBLE PRECISION,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per flagged anomaly the investigation agent (agent/) has looked
-- at, from first flag through hypothesis to either a drafted report or a
-- human escalation. This is what agent/orchestrator/ writes to as it runs,
-- what agent/tools/incident_history.py reads from ("check for similar past
-- incidents", MIP section 4.1), and what backend/api/ serves to the
-- dashboard's incident list and per-vessel drill-down (Sem5IP.md section 2).
--
-- evidence and tool_call_log are JSONB rather than normalized tables
-- deliberately: each tool (track_history, jamming_zones, incident_history)
-- returns a different shape, and MIP section 4.2's auditability
-- requirement ("every agent action logged") only needs the log readable
-- and replayable, not queryable by sub-field at Stage 1.
CREATE TABLE IF NOT EXISTS incidents (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mmsi              BIGINT NOT NULL,
    flagged_at        TIMESTAMPTZ NOT NULL,
    window_start      TIMESTAMPTZ,
    window_end        TIMESTAMPTZ,
    flagged_position  GEOGRAPHY(POINT, 4326),
    anomaly_score     DOUBLE PRECISION NOT NULL,
    anomaly_type      TEXT,
    hypothesis        TEXT NOT NULL DEFAULT 'unresolved'
                          CHECK (hypothesis IN (
                              'jamming', 'targeted_spoof', 'freeze_replay',
                              'equipment_fault', 'benign', 'unresolved'
                          )),
    confidence        DOUBLE PRECISION,
    status            TEXT NOT NULL DEFAULT 'escalated'
                          CHECK (status IN ('reported', 'escalated', 'resolved')),
    evidence          JSONB NOT NULL DEFAULT '{}'::jsonb,
    tool_call_log     JSONB NOT NULL DEFAULT '[]'::jsonb,
    report_text       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- agent/orchestrator/state_machine.py::form_hypothesis can return 'freeze_replay'
-- (added after this table's original CHECK list), and CREATE TABLE IF NOT EXISTS
-- won't touch an already-created table - so an existing database keeps the old
-- constraint and every freeze_replay incident INSERT would fail. Re-create the
-- constraint idempotently (this file is re-applied on every ingestion startup).
ALTER TABLE incidents DROP CONSTRAINT IF EXISTS incidents_hypothesis_check;
ALTER TABLE incidents ADD CONSTRAINT incidents_hypothesis_check CHECK (hypothesis IN (
    'jamming', 'targeted_spoof', 'freeze_replay',
    'equipment_fault', 'benign', 'unresolved'
));

CREATE INDEX IF NOT EXISTS incidents_mmsi_flagged_idx
    ON incidents (mmsi, flagged_at DESC);
CREATE INDEX IF NOT EXISTS incidents_status_idx
    ON incidents (status);
CREATE INDEX IF NOT EXISTS incidents_position_idx
    ON incidents USING GIST (flagged_position);

-- Analyst review (agent/review.py). status = 'resolved' keeps meaning "has a verdict":
-- recording a verdict sets it, which also lifts the live scorer's debounce for that vessel.
-- review_verdict is the analyst's call on what the incident really was; hypothesis stays
-- the agent's own guess so scoring/review_stats.py can compare the two.
-- ADD COLUMN IF NOT EXISTS keeps this safe to re-apply on every ingestion startup.
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS review_verdict TEXT
    CHECK (review_verdict IN ('confirmed_spoof', 'jamming', 'equipment_fault', 'benign', 'unclear'));
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reviewed_by TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS review_notes TEXT;

CREATE INDEX IF NOT EXISTS incidents_unreviewed_idx
    ON incidents (flagged_at DESC) WHERE review_verdict IS NULL;

-- Known jamming/spoofing zones. Stage 1: manually curated
-- (data/jamming_zones/), queried by agent/tools/jamming_zones.py to check
-- a flagged position/time against a known zone (MIP section 4.1). Stage 2
-- adds automated ingestion from public advisories (MIP section 8.3) into
-- this same table - `source` distinguishes the two without a schema change.
CREATE TABLE IF NOT EXISTS jamming_zones (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT,
    zone        GEOGRAPHY(MULTIPOLYGON, 4326) NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual'
                    CHECK (source IN ('manual', 'automated')),
    confidence  DOUBLE PRECISION,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen  TIMESTAMPTZ,
    last_seen   TIMESTAMPTZ,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jamming_zones_geo_idx
    ON jamming_zones USING GIST (zone);
CREATE INDEX IF NOT EXISTS jamming_zones_active_idx
    ON jamming_zones (active);
