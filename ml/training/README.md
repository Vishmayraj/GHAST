# ml/training/

Training scripts and MLflow experiment configs, so every model iteration has a tracked, comparable run.

## 1. Verify that live AIS data is eligible

Run these commands from `C:\Projects\GHAST\infra\docker`. They use the
TimescaleDB container directly, so they work even when PostgreSQL is not
installed on the host.

```powershell
docker compose exec timescaledb psql -U ghast -d ghast -c "SELECT count(*) AS reports, count(DISTINCT mmsi) AS vessels, min(received_at) AS first_report, max(received_at) AS last_report, max(received_at) - min(received_at) AS coverage FROM vessel_position;"
docker compose exec timescaledb psql -U ghast -d ghast -c "SELECT count(*) AS usable_vessels FROM (SELECT mmsi FROM vessel_position WHERE received_at >= now() - interval '14 days' GROUP BY mmsi HAVING count(*) >= 20) AS eligible;"
```

Do not train yet unless `coverage` is at least 14 days and `usable_vessels`
is non-zero. Keep ingestion running if either condition is false. Inspect it
with `docker compose logs --tail=100 ingestion`; do not pad or interpolate
sparse vessel tracks.

## 2. Install the ML environment

From `C:\Projects\GHAST`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r ml\requirements-dev.txt
```

If PowerShell blocks activation for a local virtual environment, use
`Set-ExecutionPolicy -Scope Process Bypass` for the current shell, then run
the activation command again.

## 3. Run normal training

The host connects to the published PostgreSQL port, so `localhost` is correct
here (use `timescaledb` only from another Compose container). Run from
`C:\Projects\GHAST\ml` and replace the dates with a completed 14-day window.

```powershell
$env:POSTGRES_DSN = "postgresql://ghast:ghast@localhost:5432/ghast"
python -m training.train --dsn $env:POSTGRES_DSN --start 2026-09-01T00:00:00+00:00 --end 2026-09-15T00:00:00+00:00 --epochs 10
```

The script trains only on clean windows produced from `vessel_position`; it
does not use injected trajectories as training inputs. A successful run prints
the final MSE loss. Preserve the command, data window, commit SHA, and output
with the experiment record for comparison.

## 4. Run the fixture-only tests

These tests do not connect to Docker, TimescaleDB, MLflow, or an API. From
`C:\Projects\GHAST\ml`, with the virtual environment active:

```powershell
python -m pytest features\tests -v
python -m pytest models\bilstm\tests -v
python -m pytest evaluation\tests -v
```

To match CI more closely, run all ML tests in one command:

```powershell
python -m pytest features\tests models\bilstm\tests evaluation\tests -v
```

## 5. Manual data-path test after training

First confirm that synthetic injection can be built from the same window. This
is intentionally a local/manual step because it reads the real database:

```powershell
python -c "import asyncio; from datetime import datetime; from features.pipeline import load_training_windows; from features.inject import build_synthetic_dataset; w=asyncio.run(load_training_windows('postgresql://ghast:ghast@localhost:5432/ghast', datetime.fromisoformat('2026-09-01T00:00:00+00:00'), datetime.fromisoformat('2026-09-15T00:00:00+00:00'))); print(f'{len(w)} clean windows, {len(build_synthetic_dataset(w, seed=0))} injected/control windows')"
```

This is not a training-quality pass. A release-quality model result additionally
requires a thresholded injected-synthetic harness result that beats the speed
jump baseline; record both result objects before promoting a model.
