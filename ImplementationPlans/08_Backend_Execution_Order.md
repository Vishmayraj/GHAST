# 08 Backend execution order for plans 01 to 06

This is the map for the next Claude. Plans 01 to 06 say what to build. This file says in what order, what blocks what, what to decide first, and what to watch for. Read the six plans first. Read `07_Frontend_Rebrand.md` for the frontend side of plans 02 and 01 D.

Plans 01 to 06 are not in this repo yet. The owner has them locally. Put them in `ImplementationPlans/` before starting.

## Ground rules for this repo

- Commit every major milestone with a one-line conventional commit message. Author `Vishmayraj <zalavishmayraj@gmail.com>`. Then hand over patch files to apply with `git am`, in order.
- Do not `pip install` the requirements. They can pass 3 GB. Verify by reading the code and by running only what already runs without heavy installs. If something needs the database, a checkpoint, or a GPU, write it, unit-test it with fakes where the plan allows, and say plainly which parts were never run.
- Every plan ends with a "not run without X" section. Fill it in honestly in your final message.
- Update `docs/` and the test counts in `docs/testing.md` as each plan says. Docs are part of done.
- No accuracy claims anywhere without reviewed-incident data behind them (plan 01 and 05 both say this).

## One change to plan 02

Plan 02 specifies a React and Vite dashboard. The owner has decided the frontend stays vanilla HTML and JS. So:

- `frontend/dashboard/` becomes vanilla JS, with MapLibre GL loaded from a script tag (cdnjs has it) and no build step.
- The visual and structural spec for the dashboard is the Console page from plan 07. Build the real dashboard by taking `console.html` and its modules and pointing the data facade at the API (`source: "live"`). Do not start a second design.
- The FastAPI side of plan 02 is unchanged. The response field names in plan 07's "Sample payload shapes" were chosen to match it. If the API differs, change the sample files, not the modules.
- The base-map tile decision in plan 02 is still open and blocks the MapLibre swap only. Everything else proceeds with the SVG map from plan 07.

## Dependency map

- 01 A (measure real traffic) needs Postgres and `epoch_010.pt`. It gates 01 B, 05 tie-breaking, and any real alert-budget number.
- 01 C (agent fixes) and 01 D (review loop) need no database. They gate 03 B wiring, 04, and the review endpoint in 02.
- 06 step 2 (migrations) should land before 01 D's schema change and 02's database role. Otherwise those `ALTER` statements get written into `schema.sql` and rewritten again.
- 02 read endpoints need nothing. 02's review endpoint needs 01 D.
- 03 A (zone loader) needs nothing. 03 B needs the 01 C `incident_history` split and the `form_hypothesis` rework.
- 04 needs 01 D (review queue and verdicts) and the fixed Laya vote rule from 01 C4. Labeling can start earlier.
- 05 stands alone for the label-free checks. It needs a database and real windows to train.
- 06 is mostly independent. Steps 1, 3 and 7 need real infrastructure.

## Waves

Each wave ends in commits and patches. Waves can overlap where the map above allows.

**Wave 0, foundations (no database).**
06 step 2 migrations and runner. 06 step 5 normalizer hygiene. 06 step 6 healthchecks in compose. 06 step 10 secrets into `.env.example`. These touch the least and unblock the rest.

**Wave 1, make the agent honest (no database).**
01 Part C, all six fixes, each with a fake-based test. Write the `benign` rule into `docs/agent.md` after the owner decides it (see decisions). Then 01 Part D: schema through the new migration system, `agent/review.py`, `scoring/review_stats.py`. Also 03 A: the zone file format, the loader with `--dry-run`, and its tests. Curate zones only from public sources with URLs, nothing invented.

**Wave 2, delivery layer.**
02 backend: FastAPI app, models, all endpoints, API key, database role, tests with a fake pool, CI workflow, compose service. Include the read endpoint for review stats that plan 07's Trust page needs. Then 03 B: the pure clustering function, the `fleet_context` tool, the `form_hypothesis` wiring, tests with hand-made reports. Then 06 step 7 SQL tests in CI, which will catch mistakes in the new queries.

**Wave 3, dashboard wiring.**
Point the plan 07 console at the API. Run the frontend from nginx with the API beside it. Then start 04 labeling tooling and 05 experiment scaffolding (`ml/experiments/`, configs, runner).

**Wave 4, needs real infrastructure.**
01 A and B with the database and checkpoint. 03 B parameter tuning on live and April data. 05 training runs. 04 fine-tune and shadow mode. 06 artifacts store and `fetch_artifacts.py`, then the archive replay tool. Do these when the owner has the services up. Until then leave their status rows unchanged.

## Decisions the owner must make

Collect them early and ask once. Suggested defaults in brackets, so work is not blocked.

1. Alert budget, flags per 1,000 reports the owner can review (01 B). [1 percent, revisit after live numbers]
2. Separate live and historical thresholds, or one (01 B). [one, unless the two reports differ a lot]
3. The `benign` rule for a lone, slightly-over-threshold `prediction_error` flag on a stationary window (01 C5). [resolve to `unresolved` first, `benign` only with a reviewed example]
4. Base map tile source for the dashboard (02). [self-hosted style, no key in the repo]
5. Whether the marketing mock stays labeled sample data or later reads the API (02). [stays labeled sample data; the console page is the one that goes live]
6. The Laya label set, after seeing how many real examples exist per class (04). [decide after the queue is built]
7. Artifact store: MinIO bucket, GitHub release assets, or other (06). [GitHub release assets, simplest for a fresh clone]
8. Stage 3 auth is out of scope. API key only for now (02).

## Traps and cross-plan details

- The frontend vocabulary is fixed. Hypotheses `jamming`, `targeted_spoof`, `equipment_fault`, `freeze_replay`, `benign`. Verdicts `confirmed_spoof`, `jamming`, `equipment_fault`, `benign`, `unclear`. Statuses `reported`, `escalated`, `resolved`. If 01 C changes a hypothesis name, tell the frontend plan (change the sample files in one place).
- Plan 01 D says `status = 'resolved'` means "has a verdict" and lifts the debounce. The API and the console both depend on that meaning. Do not add a separate status for reviewed.
- Plan 02 says track and incident JSON must never include raw `evidence` for other vessels. The fleet context panel needs counts and isolation, not other vessels' evidence. Make `fleet_context` return only `{cluster_vessels, radius_km, isolated}`.
- Plan 02's database role needs UPDATE on the review columns only. That role grant belongs in a migration, not in `schema.sql` by hand.
- The scorer only sees live rows (01 B). If per-source thresholds are chosen, write that next to the threshold so nobody wonders why the historical number is unused.
- Plan 03 and plan 05 both tune without labels. Neither may claim accuracy. Say "clusters per day" and "flag-rate ratio", nothing more.
- Plan 04 shadow mode needs a config flag read by `_apply_single_detector_cap`. The console's detector votes table shows the Laya row with a "shadow" tag until that flag is turned on.
- Plan 06's `imported_files` and dedupe index change ingestion behavior. Run the migration on a copy of real data before calling it done, or state that it was not run.
- When the API exists, update `docs/backend-and-frontend.md` from the code. It currently says there is no backend. Plan 07 updates only its site half; plan 02 rewrites the rest.

## Final report format

For each plan, say: what was written, what was unit-tested with fakes, what was never run and why, and what decisions were assumed. Keep it short. List the patch files in apply order.
