# Infrastructure

What `infra/docker/docker-compose.yml` starts, how the pieces talk to each other, which environment variables each component reads, and what you need installed to do each job. Read from the compose file, Dockerfiles, `.env.example` and the code that reads the variables.

## Compose services

Run from `infra/docker/`:

```text
cp .env.example .env      # set AISSTREAM_API_KEY, compose refuses to start without it
docker compose up -d
```

| Service | Image / build | Host ports | Depends on | Notes |
|---|---|---|---|---|
| `timescaledb` | `timescale/timescaledb-ha:pg16` | `${POSTGRES_PORT:-5432}` to 5432 | none | volume `timescaledb_data` at `/home/postgres/pgdata/data` |
| `minio` | built from `infra/docker/Dockerfile` | 9000 (API), 9001 (console) | none | volume `minio_data` at `/data` |
| `ingestion` | `ingestion/Dockerfile` | none | `timescaledb`, `minio` | applies `schema.sql`, then streams AISStream |
| `scoring` | `scoring/Dockerfile` (CPU torch) | none | `timescaledb`, `ingestion` | runs `live_scorer.py --min-votes 2` |
| `frontend` | `frontend/Dockerfile` (nginx) | `${FRONTEND_PORT:-3000}` to 80 | `ingestion` | serves `frontend/site/` as static files |

Volumes mounted into `scoring`, both read-only: `ml/checkpoints` at `/checkpoints` and `ml/laya_model` at `/laya_model`.

Not in compose: ML training, evaluation, the historical importer, and a backend API (there is no backend code). Training and evaluation run on the host against the published Postgres port.

`minio` is built from source because the upstream image is no longer pulled from Docker Hub or quay.io. The Dockerfile clones `github.com/minio/minio` at tag `RELEASE.2025-10-15T17-29-55Z` and compiles it with Go 1.24, so the first build needs network access to GitHub and takes minutes. The header comment in that Dockerfile has the reasoning.

`scoring` image: `python:3.12-slim`, CPU-only torch from the PyTorch CPU wheel index, `scoring/requirements.txt`, then optionally `scoring/requirements-laya.txt` when built with `WITH_LAYA=1`. It copies `ml/`, `agent/` and `scoring/` into `/app` and runs from `/app/scoring`. The `ml/` copy includes `ml/laya_model/` (about 3.5 MB of tokenizer files; no weights, see `docs/laya_pattern_classifier.md`).

## How the services communicate

```text
AISStream (wss, external) --> ingestion --> timescaledb   asyncpg, DSN host "timescaledb"
                                        --> minio         minio SDK, endpoint "minio:9000"
timescaledb <-- scoring                                   asyncpg, same DSN
review CLI / backend --> Groq API (https, external, optional)   report drafting on request only
scoring --> Laya model (in process, optional)             loaded from /laya_model
frontend (nginx)                                          no connection to any other service
```

Services share only the database. `ingestion` and `scoring` do not call each other. `depends_on` orders container start, it does not wait for readiness (no `healthcheck` is defined anywhere), so `scoring` can start before `ingestion` has applied the schema. Its first poll then fails with a missing-relation error, which `run_forever()` logs and retries on the next interval. The `frontend` `depends_on: ingestion` has no functional basis, the site makes no requests.

Host tools (training, evaluation, importer, running the scorer outside Docker) connect to `localhost:5432`.

## Environment variables

Compose reads `infra/docker/.env`. Host-run Python code reads the process environment; `scoring/live_scorer.py` additionally loads a `.env` from the repository root (not `infra/docker/.env`) with `python-dotenv`, without overriding variables already set.

| Variable | Read by | Default | Notes |
|---|---|---|---|
| `AISSTREAM_API_KEY` | ingestion | none, required | compose uses `${...:?}` so `up` fails if unset |
| `AISSTREAM_BOUNDING_BOXES` | ingestion | whole planet | JSON `[[[lat,lon],[lat,lon]], ...]` |
| `AISSTREAM_FILTER_MMSI` | ingestion | none | comma separated, AISStream allows 200 |
| `AISSTREAM_FILTER_MESSAGE_TYPES` | ingestion | the four types in `data-pipeline.md` | comma separated |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT` | compose | `ghast`, `ghast`, `ghast`, `5432` | used to build the container and the DSNs |
| `POSTGRES_DSN` | ingestion, importer, scorer; `--dsn` in ML CLIs | `from_env` uses `timescaledb:5432`, importer uses `localhost:5432`, scorer has none | compose sets it for `ingestion` and `scoring` itself |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | minio, ingestion | `ghast`, `ghast12345` | |
| `MINIO_ENDPOINT` | ingestion | `minio:9000` | |
| `MINIO_RAW_BUCKET` | ingestion | `ghast-raw-ais` | |
| `MINIO_SECURE` | ingestion | `false` | not in `.env.example` or compose |
| `MINIO_API_PORT`, `MINIO_CONSOLE_PORT` | compose | 9000, 9001 | |
| `FRONTEND_PORT` | compose | 3000 | |
| `GHAST_CHECKPOINT` | scorer | none | compose hardcodes `/checkpoints/epoch_010.pt` |
| `GHAST_LAYA_MODEL` | scorer | unset means neutral stub | compose passes `${GHAST_LAYA_MODEL:-}` |
| `WITH_LAYA` | scoring image build arg | 0 | `1` installs `laya` |
| `GROQ_API_KEY` | on-demand report drafting (`agent/review.py report`), not the scorer | unset means no reports can be drafted | no longer passed to the scoring container |
| `GHAST_REPORT_MODEL` | `report_generator/report.py` | `openai/gpt-oss-120b` | |
| `GHAST_REPORT_MAX_TOKENS` | `report_generator/report.py` | 1200 | |
| `MLFLOW_TRACKING_URI` | `score_checkpoint.py` | `sqlite:///mlruns/mlflow.db` | path is relative to the working directory, so `ml/mlruns/` when run from `ml/` |

`.env.example` also lists `POSTGRES_DSN="postgresql://ghast:ghast@localhost:5432/ghast"`. That value is for host tools; compose ignores it for containers and builds its own DSN.

## What you need for each job

| Job | Needs | Does not need |
|---|---|---|
| Run the unit tests | Python 3.12 and the requirements for the package under test | Postgres, GPU, checkpoint, network, API keys |
| Ingest live AIS | Docker, an AISStream key, outbound websocket access | GPU, Python on the host |
| Import the April backfill | Postgres with the schema applied, the CSVs in `data/raw/`, `pandas` and `psycopg` | ingestion running (it only has to have applied the schema once) |
| Train | Postgres with data, `ml/requirements-dev.txt`, run from `ml/` | GPU (used if present, CPU otherwise), MLflow (training does not use it) |
| Score a checkpoint offline | Postgres with data, a `.pt` checkpoint, run from `ml/` | GPU, MLflow (`--no-mlflow` skips it) |
| Export the Laya dataset | Postgres with data, run from `ml/` | torch, a checkpoint |
| Run the live scorer | Postgres with live data, `epoch_010.pt` (or another checkpoint), torch | GPU (CPU by default), Groq key, Laya |
| Turn Laya on | `pip install laya`, a fine-tuned model directory including `model.safetensors`, `--laya-model` or `GHAST_LAYA_MODEL` | |

Run directory matters. The `ml/`, `agent/` and `scoring/` packages use flat imports (`from features.extract import ...`), not `ml.features...`. Commands like `python -m training.train` and `python -m evaluation.score_checkpoint` work only with `ml/` as the working directory (or on `PYTHONPATH`). `scoring/live_scorer.py` appends `ml/` and `agent/` (relative to its own location) to `sys.path`, so it needs no `PYTHONPATH`; the pytest configs do the same with `pythonpath` in each `pytest.ini`.

CUDA: nothing in the code requires it. `training.train` and `score_checkpoint` choose `cuda` when `torch.cuda.is_available()`, else `cpu`, and `--device` overrides. `live_scorer.py` defaults to `cpu`. The scoring container installs CPU-only torch.

The existing `.gitignore` excludes `*.pt`, `*.safetensors`, `.env`, `infra/docker/.env`, `mlruns/` and `data/raw/*`. So a fresh clone has no BiLSTM checkpoint, no Laya weights, no raw data and no MLflow history.

## Known issues

- `infra/docker/README.md` points at `infra/docker/minio/Dockerfile`; the file is `infra/docker/Dockerfile`. The same README says `ml/`, `backend/` and `frontend/` are empty, which is no longer true for `ml/` and `frontend/`.
- The `ingestion` service builds its DSN as `postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@...` with no defaults. If `POSTGRES_USER` and `POSTGRES_PASSWORD` are missing from `.env`, the DSN is malformed. The `scoring` service and the `timescaledb` service do have `:-ghast` defaults.
- `GHAST_CHECKPOINT` is fixed to `epoch_010.pt` in compose. Using another checkpoint means editing the compose file. If `ml/checkpoints/` does not exist on the host, Docker creates an empty directory for the bind mount and the scorer fails at `torch.load`.
- No healthchecks, so start-order races described above are handled by retry, not by waiting.
- Postgres credentials and the MinIO root password default to `ghast` / `ghast12345` and the database port is published on the host. Fine for a local dev stack, not a deployment configuration.
- `terraform/` and any cloud deployment are not present (the plans defer them to Stage 3).
