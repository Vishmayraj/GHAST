# 02 Delivery layer: API and analyst dashboard

Depends on: 01 for the review endpoint (the read endpoints can start immediately).

## Why

The HLD's fourth layer does not exist. Incidents are rows nobody can see. This is also the remaining Semester 5 deliverable (weeks 14 to 16 on the HLD calendar).

## Backend (`backend/api/`, FastAPI, asyncpg)

Read-only unless stated. Reuse the pool pattern from `scoring/live_scorer.py`. Add `backend/requirements.txt` and `backend/Dockerfile`.

| Endpoint | Returns |
|---|---|
| `GET /health` | database reachable, latest `vessel_position` age, latest incident age |
| `GET /incidents?status=&hypothesis=&mmsi=&limit=&before=` | list, newest first, keyset pagination on `flagged_at` |
| `GET /incidents/{id}` | one incident with `evidence`, `tool_call_log`, `report_text` |
| `GET /vessels/{mmsi}/track?from=&to=` | positions for the map, live rows by default, `include_historical=true` to add April |
| `GET /vessels/{mmsi}/incidents` | that vessel's history |
| `GET /zones` | active jamming zones as GeoJSON (empty until plan 03) |
| `POST /incidents/{id}/review` | verdict and notes (plan 01 schema); the only write |

Rules: a database role with SELECT on everything and UPDATE only on the review columns; simple API key from an env var on every route (real auth is Stage 3); response models with Pydantic so the dashboard has a typed contract. Track and incident JSON must never include raw `evidence` for other vessels.

Tests: `backend/tests/` with a fake pool, one test per endpoint plus pagination and the review write. Add `.github/workflows/backend-tests.yml`. Add a `backend` service to compose (port 8000, depends on timescaledb).

## Dashboard (`frontend/dashboard/`, React, Vite, MapLibre GL)

Three views, no more:

1. Map: recent incidents as markers colored by status, click opens the incident panel. Vessel track drawn as a line when a vessel is selected. Zone polygons layer (hidden when empty).
2. Incident list: filter by status and hypothesis, sortable by confidence and time.
3. Incident detail: hypothesis, confidence, detector votes, the tool call log as a readable timeline, the freeze and Laya evidence, the stored markdown report, and the review form.

Base map tiles need a source that allows use; decide with the owner (a self-hosted style is preferable to an API key in the repo). No mock data in the dashboard: it reads the API or shows an empty state.

Serve the built dashboard from nginx next to the static site, or as its own compose service. Vite dev proxy to the API for local work.

## Marketing site

`frontend/site/scripts/mock-data.js` depicts features that do not exist (DBSCAN check, zone counts, meter deviations, auto-dismissal). Owner decision: keep it labeled as an illustrative mock on every page that uses it, or feed the demo widgets from the real API once it exists. Do not leave it unlabeled.

## Done when

- `docker compose up` brings up the API and dashboard; the dashboard lists real incidents from the database.
- API tests run in CI without a database.
- `docs/backend-and-frontend.md` is rewritten from the code (it currently says there is no backend), and `README.md` and `backend/README.md` match.

## Not run without a live database

The dashboard against real data needs Postgres with incidents. State whether it was only built and type-checked.
