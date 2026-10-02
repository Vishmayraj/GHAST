# backend/api/

FastAPI app. Run it from this directory:

```
GHAST_API_KEY=... POSTGRES_DSN=postgresql://ghast:ghast@localhost:5432/ghast uvicorn main:create_app --factory --port 8000
```

Optional: `GHAST_CORS_ORIGINS` (comma separated) when the site is on a different origin. Routes and response shapes are in `docs/backend-and-frontend.md`.
