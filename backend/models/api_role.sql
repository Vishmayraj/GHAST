-- Database role for the API (backend/api). Run once by hand as a superuser, it is not part of
-- schema.sql because plan 06 moves this kind of change into migrations. Not run in the session
-- that wrote it (no database). Change the password first.
--
-- Everything is read-only except the five review columns of incidents.
DO $$ BEGIN
  CREATE ROLE ghast_api LOGIN PASSWORD 'change-me';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

GRANT USAGE ON SCHEMA public TO ghast_api;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ghast_api;
GRANT UPDATE (review_verdict, reviewed_by, reviewed_at, review_notes, status, updated_at) ON incidents TO ghast_api;
