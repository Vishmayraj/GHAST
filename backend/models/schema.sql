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

-- Reports are drafted on request, never during investigation (agent/report_generator/on_demand.py).
-- report_text stays NULL until an analyst asks for one; report_generated_at records when.
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS report_generated_at TIMESTAMPTZ;

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

-- ---------------------------------------------------------------------------------------
-- Two-threshold alerting (plan 01 follow-up). Idempotent, like everything above.
--
-- threshold_a: a flag at or above it becomes an incident on the analyst console, where anyone
--              can press a button to draft its report.
-- threshold_b: (> threshold_a) a flag at or above it is also drafted automatically and sorted
--              first in listings.
-- The threshold agent (agent/threshold_agent) writes a row whenever it changes them, because
-- both depend on the Laya/BiLSTM checkpoint in use and move every time the model is
-- fine-tuned. The newest row is the active one; older rows are the history.
CREATE TABLE IF NOT EXISTS threshold_config (
    id             BIGSERIAL PRIMARY KEY,
    threshold_a    DOUBLE PRECISION NOT NULL CHECK (threshold_a > 0),
    threshold_b    DOUBLE PRECISION NOT NULL,
    model_version  TEXT,
    set_by         TEXT NOT NULL DEFAULT 'manual' CHECK (set_by IN ('manual', 'agent', 'default')),
    reason         TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (threshold_b > threshold_a)
);
CREATE INDEX IF NOT EXISTS threshold_config_latest_idx ON threshold_config (created_at DESC);

-- One row per scorer cycle: how many new reports were scored, how many were skipped as
-- unscorable (agent/../ml/features/quality.py), and a log-spaced histogram of prediction errors
-- so the flag rate at ANY candidate threshold can be computed later without keeping a row per
-- report. Edges are fixed in code (scoring/score_stats.py::HISTOGRAM_EDGES).
CREATE TABLE IF NOT EXISTS scoring_stats (
    id                  BIGSERIAL PRIMARY KEY,
    scored_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_version       TEXT,
    reports_scored      INTEGER NOT NULL,
    skipped_unscorable  INTEGER NOT NULL DEFAULT 0,
    flagged_a           INTEGER NOT NULL DEFAULT 0,
    flagged_b           INTEGER NOT NULL DEFAULT 0,
    histogram           JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS scoring_stats_time_idx ON scoring_stats (scored_at DESC);

-- Audit trail for every agent decision: the task, each tool call and result, the decision,
-- and whether the LLM or the deterministic fallback made it (agent/runtime/agent.py).
CREATE TABLE IF NOT EXISTS agent_runs (
    id           BIGSERIAL PRIMARY KEY,
    agent        TEXT NOT NULL,
    mode         TEXT NOT NULL CHECK (mode IN ('llm', 'fallback')),
    task         JSONB NOT NULL DEFAULT '{}'::jsonb,
    steps        JSONB NOT NULL DEFAULT '[]'::jsonb,
    decision     JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason       TEXT,
    incident_id  UUID,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_runs_agent_time_idx ON agent_runs (agent, created_at DESC);
CREATE INDEX IF NOT EXISTS agent_runs_incident_idx ON agent_runs (incident_id) WHERE incident_id IS NOT NULL;

-- Incident tier and report lifecycle. Incidents and raw positions are kept forever (data for
-- later training); only the drafted report text expires.
--   tier 'A'  shown on the console, report on request
--   tier 'B'  report drafted automatically, listed first
--   priority  set by the triage agent within a tier (higher = look first)
--   report_expires_at  24 hours after the draft; the retention job clears report_text then
--   report_deleted_at  set when an analyst deletes a report on purpose; the same column is set
--                      by expiry, report_delete_reason says which ('analyst' or 'expired').
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'A' CHECK (tier IN ('A', 'B'));
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS priority DOUBLE PRECISION;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS report_auto BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS report_expires_at TIMESTAMPTZ;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS report_deleted_at TIMESTAMPTZ;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS report_delete_reason TEXT
    CHECK (report_delete_reason IN ('analyst', 'expired'));
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS challenge JSONB;

CREATE INDEX IF NOT EXISTS incidents_console_idx
    ON incidents (tier DESC, priority DESC NULLS LAST, flagged_at DESC) WHERE review_verdict IS NULL;
CREATE INDEX IF NOT EXISTS incidents_report_expiry_idx
    ON incidents (report_expires_at) WHERE report_text IS NOT NULL;

-- Result of the report verifier agent for the stored draft (agents/report_verifier.py):
-- {"verdict": "pass"|"fail", "issues": [...], "attempts": n}. A failing draft is stored with a
-- visible warning at the top of report_text, and this column says why.
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS report_verification JSONB;
