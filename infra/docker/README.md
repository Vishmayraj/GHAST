# infra/docker/

The go-to place for getting this project running locally, both as a user and as a contributor.

## As a user (just want it running)

```
cd infra/docker
cp .env.example .env
```

Edit `.env` and set `AISSTREAM_API_KEY` (a free key from https://aisstream.io) - the ingestion service won't start without it. Everything else has a working default.

```
docker compose up -d
```

The first run builds MinIO from source (a few minutes, see
`infra/docker/minio/Dockerfile` for why) - it's not pulled as a
pre-built image, since minio/minio is no longer available on Docker
Hub or quay.io. After the first build it's cached like any image.

This brings up:
- **TimescaleDB** (Postgres + PostGIS + TimescaleDB) on `localhost:5432` - where vessel tracks and incidents live.
- **MinIO** (S3-compatible object storage) on `localhost:9000` (API) / `localhost:9001` (console) - where the raw AIS archive lives.
- **ingestion** - connects to AIS Stream, normalizes messages, and writes them into TimescaleDB, archiving the raw envelopes to MinIO alongside.

Check it's actually receiving data with `docker compose logs -f ingestion`, or query TimescaleDB directly:

```
docker compose exec timescaledb psql -U ghast -d ghast -c "select count(*) from vessel_position;"
```

Stop everything with `docker compose down` (add `-v` to also drop the volumes and start clean).

## As a contributor (adding a new service)

`ml/`, `backend/`, and `frontend/` are still empty layers being filled in per `ImplementationPlans/Sem5IP.md`. As each one gets its first real code, add it here the same way `ingestion` was added:

1. Add a `Dockerfile` next to the service's code (e.g. `backend/Dockerfile`).
2. Add a matching service block to `docker-compose.yml`, using `timescaledb` / `minio` as `depends_on` where relevant.
3. Keep this compose file as the single command that gets a fresh clone running - if a service needs a new env var, add it to `.env.example` too so it stays documented.

Don't add a service block for something that has no code yet - an empty scaffold directory doesn't need a Dockerfile.
