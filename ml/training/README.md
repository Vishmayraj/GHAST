# ml/training/

Training scripts and MLflow experiment configs, so every model iteration has a tracked, comparable run.

## 1. Verify that live AIS data is eligible

Run these commands from `C:\Projects\GHAST\infra\docker`. They use the
TimescaleDB container directly, so they work even when PostgreSQL is not
installed on the host.

```powershell
docker compose exec timescaledb psql -U ghast -d ghast -c "SELECT count(*) AS live_reports, count(DISTINCT mmsi) AS live_vessels, min(received_at) AS first_live_report, max(received_at) AS latest_live_report, max(received_at) - min(received_at) AS live_coverage FROM vessel_position WHERE message_type IS DISTINCT FROM 'historical';"
docker compose exec timescaledb psql -U ghast -d ghast -c "SELECT count(*) AS usable_live_vessels FROM (SELECT mmsi FROM vessel_position WHERE message_type IS DISTINCT FROM 'historical' AND received_at >= (SELECT min(received_at) FROM vessel_position WHERE message_type IS DISTINCT FROM 'historical') AND received_at < (SELECT min(received_at) + interval '14 days' FROM vessel_position WHERE message_type IS DISTINCT FROM 'historical') GROUP BY mmsi HAVING count(*) >= 20) AS eligible;"
```

Do not train live data until the first live report has 14 complete days of
coverage and the operational 15-day wait has elapsed. This deliberately does
not use `now() - interval '14 days'`: that range is invalid when ingestion has
only recently started. Historical MarineCadastre rows (`message_type =
'historical'`) are excluded from both checks. Keep ingestion running if either
condition is false; do not pad or interpolate sparse vessel tracks.

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
`C:\Projects\GHAST\ml`. The default initial mode discovers the live dates
itself: first live report through first live report plus 14 days.

```powershell
$env:POSTGRES_DSN = "postgresql://ghast:ghast@localhost:5432/ghast"
python -m training.train --dsn $env:POSTGRES_DSN --source live --live-window initial --epochs 10
```

After the first production run, use the latest complete live 14-day range:

```powershell
python -m training.train --dsn $env:POSTGRES_DSN --source live --live-window rolling --epochs 10
```

The script trains only on clean windows produced from `vessel_position`; it
does not use injected trajectories as training inputs. `--source historical`
is an explicit development-only path and requires its April bounds, for
example `--start 2026-04-01T00:00:00+00:00 --end 2026-04-16T00:00:00+00:00`.
It never silently mixes into live mode. Preserve the resolved data window,
commit SHA, and output with the experiment record for comparison.

## 3a. How training scales to the full historical backfill

`--source historical` over the full MarineCadastre range is ~31M rows, too
much to load with a single `connection.fetch()` or to fit as one giant
training batch. The pipeline instead:

1. Streams rows from TimescaleDB through a server-side cursor
   (`features.pipeline.stream_feature_windows`), grouped by vessel as they
   arrive, so it never holds more than one vessel's reports plus a bounded
   prefetch buffer in memory.
2. Writes windows straight to small `.npz` shards on disk
   (`training.dataset_cache.materialize_to_shards`), splitting vessels into
   train/validation by a stable hash of MMSI, so the split needs no
   up-front vessel list and never leaks a vessel across both sides.
3. Trains over those shards in mini-batches (`--batch-size`, default 256),
   loading one shard at a time and moving only the current batch onto the
   GPU if one is available.
4. Saves a checkpoint after every epoch under `--checkpoint-dir` (default
   `checkpoints/`), plus a rolling `latest.pt`.

Useful flags for a laptop-scale run:

```powershell
python -m training.train --dsn $env:POSTGRES_DSN --source historical `
    --start 2026-04-01T00:00:00+00:00 --end 2026-04-16T00:00:00+00:00 --epochs 10 `
    --batch-size 256 --max-vessels 500 --checkpoint-dir checkpoints\dev-run
```

- `--max-vessels` / `--max-windows` / `--max-rows` are explicit development
  sampling caps; leave them unset for a real training run so nothing is
  silently truncated.
- `--device cpu` or `--device cuda` overrides auto-detection.
- `--cache-dir` points at a specific shard cache directory instead of a
  fresh temp directory; useful for inspecting what got materialized.
- `--resume-from checkpoints\dev-run\latest.pt` continues from a checkpoint.
- If the machine still struggles, lower `--batch-size` first; that is the
  main memory/VRAM knob.

Progress prints throughout: an estimated row count up front (best-effort,
skipped if the count itself times out), running row/vessel/window counts
while streaming, and a running loss every 20 batches during training.

## 4. Run the fixture-only tests

These tests do not connect to Docker, TimescaleDB, MLflow, or an API. From
`C:\Projects\GHAST\ml`, with the virtual environment active:

```powershell
python -m pytest features\tests -v
python -m pytest training\tests -v
python -m pytest models\bilstm\tests -v
python -m pytest evaluation\tests -v
```

To match CI more closely, run all ML tests in one command:

```powershell
python -m pytest features\tests training\tests models\bilstm\tests evaluation\tests -v
```

## 5. Manual data-path test after training

First confirm that synthetic injection can be built from the same window. This
is intentionally a local/manual step because it reads the real database:

```powershell
python -c "import asyncio; from training.train import select_live_window; from features.pipeline import load_training_windows; from features.inject import build_synthetic_dataset; dsn='postgresql://ghast:ghast@localhost:5432/ghast'; start,end=asyncio.run(select_live_window(dsn, 'initial')); w=asyncio.run(load_training_windows(dsn, start, end, 'live')); print(f'{start.isoformat()} to {end.isoformat()}: {len(w)} clean windows, {len(build_synthetic_dataset(w, seed=0))} injected/control windows')"
```

This is not a training-quality pass. A release-quality model result additionally
requires a thresholded injected-synthetic harness result that beats the speed
jump baseline; record both result objects before promoting a model.
