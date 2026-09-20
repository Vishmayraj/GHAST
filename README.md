# GHAST
## GNSS & AIS Hazardous Spoofing Tracker

Core MIP and HLD lies at `/HLD`.

Currently building Semester 5 (Stage 1) - see `ImplementationPlans/Sem5IP.md` for the detailed plan this stage is being built against.

## Quick start

```
cd infra/docker
cp .env.example .env
docker compose up -d
```

Brings up TimescaleDB and MinIO for local dev. See `infra/docker/README.md` for details, and for how to add a new service as one lands.

## Layout

| Path | What it is |
|---|---|
| `ImplementationPlans/` | Stage-scoped implementation plans (start here) |
| `data/` | Raw archive, research datasets, jamming-zone data |
| `ingestion/` | AIS feed collector + AIVDM/NMEA normalizer |
| `ml/` | Feature extraction, Bi-LSTM model, training, evaluation |
| `agent/` | Investigation agent: orchestrator, tools, report generator |
| `backend/` | FastAPI app + DB models |
| `frontend/` | React dashboard |
| `infra/` | Docker (local dev) and CI |
| `docs/` | Research notes (design doc itself stays in `HLD/`) |
| `notebooks/` | Exploration notebooks |
