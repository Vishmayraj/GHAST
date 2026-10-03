-- Provenance for curated jamming zones (scripts/load_jamming_zones.py). Every zone the loader
-- writes carries where it came from; the unique index is what makes the loader idempotent.
-- Existing rows (none, the table was empty) keep NULLs, and NULLs never conflict.
ALTER TABLE jamming_zones ADD COLUMN IF NOT EXISTS source_name TEXT;
ALTER TABLE jamming_zones ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE jamming_zones ADD COLUMN IF NOT EXISTS source_date DATE;
CREATE UNIQUE INDEX IF NOT EXISTS jamming_zones_name_source_idx ON jamming_zones (name, source_url);
