-- Drop exact duplicate position reports, then make them impossible.
--
-- A duplicate is the same vessel, timestamp and position. The unique index has to contain the
-- hypertable time column, which is why it is (mmsi, received_at, latitude, longitude) and not
-- just the key one would like. Ingestion then inserts with ON CONFLICT DO NOTHING.
--
-- The DELETE self-joins vessel_position. On the 6 million row April import it is slow, and it
-- has never been run against real data. Run it in a quiet period. ctid ordering is only
-- meaningful inside one chunk, which is enough here: equal timestamps live in the same chunk.
DELETE FROM vessel_position a
USING vessel_position b
WHERE a.ctid > b.ctid
  AND a.tableoid = b.tableoid
  AND a.mmsi = b.mmsi
  AND a.received_at = b.received_at
  AND a.latitude = b.latitude
  AND a.longitude = b.longitude;

CREATE UNIQUE INDEX IF NOT EXISTS vessel_position_dedupe_idx
    ON vessel_position (mmsi, received_at, latitude, longitude);
