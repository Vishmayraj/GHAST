-- Stage 1 core tables: vessel positions (hypertable) + latest static data.
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
