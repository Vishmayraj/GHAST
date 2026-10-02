# Backend and frontend

The backend is a small FastAPI app over the `incidents` table. The frontend is a static vanilla JS site whose console page is the analyst dashboard.

## Backend (`backend/api/`)

| File | What it is |
|---|---|
| `main.py` | `create_app(pool, api_key)`: routes, API key check, CORS, the SQL constants |
| `models.py` | Pydantic request and response models, the typed contract the console reads |
| `shaping.py` | pure functions that turn stored `evidence` and `tool_call_log` into sentences, detector votes and fleet context |

Start it with `cd backend/api && GHAST_API_KEY=... POSTGRES_DSN=... uvicorn main:create_app --factory --port 8000`. It exits at start if `GHAST_API_KEY` is empty. In compose it is the `backend` service on `${BACKEND_PORT:-8000}`.

Every route requires the `X-API-Key` header, compared in constant time. The key is a stopgap; real auth is Stage 3.

| Endpoint | Returns |
|---|---|
| `GET /health` | `database`, age in seconds of the latest live position and of the latest incident. 503 if the database does not answer |
| `GET /incidents?status=&hypothesis=&mmsi=&limit=&before=` | `{items, next_before}`, newest first. `before` is a `flagged_at` cursor. Values outside the fixed vocabularies are 422 |
| `GET /incidents/{id}` | summary plus `window_start/end`, `anomaly_score`, `votes`, `evidence` (sentences), `fleet_context`, `challenge`, `tool_call_log` (tool and one-line summary), `report_text`, `report_verification`, `track` |
| `GET /vessels/{mmsi}/track?from=&to=&include_historical=` | live positions, last 24 hours by default; `include_historical=true` adds the April import |
| `GET /vessels/{mmsi}/incidents` | that vessel's incidents |
| `GET /zones` | active jamming zones as GeoJSON (empty until plan 03 loads some) |
| `GET /review-stats` | the same numbers as `scoring/review_stats.py` (it is imported, not copied), plus the minimum of 30 |
| `GET /thresholds` | the active `threshold_config` row and the history |
| `POST /incidents/{id}/review` | body `{verdict, notes?, reviewer?}`. Sets `status = 'resolved'` like `agent/review.py`. 409 if the incident already has a verdict, 404 if it does not exist. The only write |

What the API does not return, on purpose:

- Raw `evidence` and raw `tool_call_log`. They hold other vessels' rows (`incident_history.same_pattern_elsewhere`) and whole track dumps. The console gets sentences and counts. Fleet context is counts only.
- Predicted positions. The model's predictions are not stored, so `track` points have `predicted_lat` and `predicted_lon` as `null` and the map draws only the reported track.
- A report-drafting endpoint. Reports are still drafted with `agent/review.py report <id>` (or automatically for tier B). `report_text` is `null` until then.

`fleet_context.radius_km` is `null`: the fleet-context agent can widen its radius and does not store which one it used. `isolated` is `null` when the agent said it had too few neighbours to judge.

Database role: `backend/models/api_role.sql` creates `ghast_api` with SELECT everywhere and UPDATE only on the review columns. It is a manual script, not run by anything, and the compose stack connects with the main database user. Plan 06 should turn it into a migration.

## Frontend (`frontend/site/`)

Six HTML pages, CSS and small scripts. No framework, no build step. Design rules and the handover guide are in `docs/frontend/`.

All data goes through one facade, `GHAST.data` (`scripts/data/api.js`), which resolves to `sample.js` or `live.js` from `scripts/config.js`. `?source=live` switches one visit. The console and the trust page are the analyst dashboard: in live mode they read the API, and the "Sample data" tags disappear because they only show for the sample source.

Under compose, nginx (`frontend/nginx/default.conf.template`) serves the site and proxies `/api/` to the backend, adding the API key header. So the page needs no key and no CORS. The consequence is that whoever can reach the frontend port can use the API, including the review endpoint.

The map is still the SVG map from the Console page. Swapping it for MapLibre waits on the base-map tile decision in `PRODUCT.md`.

## Status

| Piece | State |
|---|---|
| database schema | implemented (`backend/models/schema.sql`) |
| API endpoints, key check | implemented, 29 tests with a fake pool, never run against a real database |
| database role | script only, not applied |
| console and trust page on live data | wired, never opened against a running API |
| MapLibre map | not started, blocked on the tile decision |
| report-drafting endpoint | not written |
