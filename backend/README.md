# backend/

The API over the `incidents` table, and the database schema.

- `api/` - FastAPI app (`main.py`, `models.py`, `shaping.py`). Endpoint list and what it leaves out: `docs/backend-and-frontend.md`.
- `models/` - `migrations/` (numbered SQL, applied by `scripts/migrate.py` and by ingestion on start), `schema.sql` (generated snapshot) and `api_role.sql` (manual, creates the API's database role).
- `tests/` - API tests with a fake pool. `cd backend && pytest tests`.

Stage 1 auth is one API key from `GHAST_API_KEY`. OAuth2/JWT and per-customer keys are a Stage 3 item (MIP section 8.4).
