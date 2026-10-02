# GHAST
## GNSS & AIS Hazardous Spoofing Tracker

Core MIP and HLD lie in `docs/HLD/`. For what the code does today, start with `docs/architecture.md`.

Currently building Semester 5 (Stage 1). What is built and what is missing against the design: `docs/hld-vs-current.md`. What to build next: `ImplementationPlans/README.md`.

## Quick start

```
cd infra/docker
cp .env.example .env
docker compose up -d
```

Brings up TimescaleDB, MinIO, ingestion, scoring, the backend API and the static frontend (which proxies `/api/` to the backend). Set `GHAST_API_KEY` in `.env` first. Ingestion needs `AISSTREAM_API_KEY` in `.env`, and scoring needs `ml/checkpoints/epoch_010.pt` (not in the repo). See `infra/docker/README.md` for details, and for how to add a new service as one lands.

## Layout

| Path | What it is |
|---|---|
| `ImplementationPlans/` | Current implementation plans, in order (`old/` holds superseded plans) |
| `data/` | Raw archive, research datasets, jamming-zone data |
| `ingestion/` | AIS feed collector + AIVDM/NMEA normalizer |
| `ml/` | Feature extraction, Bi-LSTM model, training, evaluation |
| `agent/` | Investigation agent: orchestrator, tools, report generator |
| `backend/` | DB schema (`models/schema.sql`) and the FastAPI app over `incidents` (`api/`) |
| `frontend/` | `site/`: the public site and the analyst console, plain HTML and JS |
| `infra/` | Docker (local dev) and CI |
| `docs/` | Research notes (design doc itself stays in `HLD/`) |
| `notebooks/` | Exploration notebooks |
