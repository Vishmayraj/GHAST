# backend/

The API side of the delivery layer: exposes the scoring results, incident feed, and dashboard data.

- `api/` — FastAPI app: scoring endpoint, incident feed, dashboard data.
- `models/` — DB schema and ORM models.

Stage 1 stays unauthenticated/single-tenant; OAuth2/JWT and per-customer API keys are a Stage 3 item (MIP section 8.4) once there's a real pilot customer.
