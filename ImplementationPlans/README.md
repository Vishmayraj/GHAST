# Implementation plans

Current plans, in the order to do them. The five earlier plans (evaluation, big pass, manifesto, Sem 5 plan) are in `old/`. They are history: several of their status lines are stale and they describe a synthetic evaluation that no longer exists.

Read `docs/hld-vs-current.md` first. It is the reason these plans exist: it lists what the HLD promised, what is built, and what is missing.

## The plans

| Plan | What it delivers | Depends on | HLD gap it closes |
|---|---|---|---|
| `01_Trust_Pass.md` | a real-traffic threshold, agent rule fixes, and an analyst review loop that produces the first real quality numbers | nothing | no quality measurement; escalation ends in a column |
| `02_Delivery_Layer.md` | FastAPI backend and React/MapLibre dashboard over `incidents` | 01 (review endpoint) | no API, no dashboard, no incident feed (the remaining Semester 5 deliverable) |
| `03_Jamming_Zones_And_Fleet_Clustering.md` | curated zone data, a loader, and DBSCAN so the agent can tell area jamming from one vessel | 01 | empty zone table, no clustering |
| `04_Laya_Real_Labels.md` | a Laya classifier trained on real labeled windows, or an honest shadow-mode decision | 01 | Laya was trained on removed synthetic data |
| `05_Model_Validity.md` | label-free ablations and a v2 model that fixes the known design problems | 01 helps, not required | bidirectional leak, no time-step input, no rate-of-turn in training |
| `06_Ops_Hardening.md` | artifacts outside laptops, migrations, dedupe, healthchecks, SQL tests in CI | nothing | not reproducible from a clone; no test runs SQL |

Suggested order against the HLD calendar (Semester 5 ends about 2026-11-21, Semester 6 starts 2027-01-15):

1. Now to mid October: `01_Trust_Pass`. Start the SQL-test and artifact-storage parts of `06_Ops_Hardening` in parallel, they touch different files.
2. Mid October to mid November: `02_Delivery_Layer`. Do the zone-loader half of `03` (small) so the dashboard has zones to show.
3. Before the Semester 5 writeup: whatever of `04` labeling has produced. The writeup can honestly say "real labels are being collected".
4. Semester 6: fleet clustering half of `03`, then `05`, then the transformer question from the HLD, gated on `05` results.

## Rules that apply to every plan

These come from decisions already made. They are not re-litigated in the plans.

- **No synthetic data.** Nothing injects, simulates or fabricates AIS reports, labels or incidents. Real windows come from Postgres. Unit tests may build small hand-made windows as fixtures, and that is all.
- **No labels means no F1.** Real windows have no ground truth. Report rates, and report precision only from analyst-reviewed incidents (plan 01). Do not restore an F1 number from any generated data.
- **Docs move with code.** Every plan's "done" includes updating the affected file in `docs/`. The docs are written from the code and treated as accurate, so a change that leaves them wrong is not done. Update `docs/testing.md` counts when tests change.
- **Say what was not run.** No plan can assume the agent executing it has the database, a GPU, the checkpoint or API keys. State plainly which steps were run and which were only written and unit-tested.
- **Uncalibrated stays labeled.** Hand-set confidences and thresholds keep their "uncalibrated" comment until a plan replaces them with something measured.

## Repo facts every session needs

- Python 3.12. The `ml/`, `agent/` and `scoring/` packages use flat imports (`from features.extract import ...`). Run tests from inside the package directory:
  ```text
  cd ml       && pytest features/tests training/tests models/bilstm/tests evaluation/tests
  cd agent    && pytest tests
  cd scoring  && pytest tests
  cd ingestion && pytest tests
  ```
- Tests use fakes for the database, the checkpoint, Groq and Laya. None needs a service.
- Data sources: `vessel_position` rows with `message_type = 'historical'` are the April 2026 MarineCadastre import. Everything else is live AISStream. There is no `source` column.
- `ml/checkpoints/epoch_010.pt` and the Laya weights are not in git. The scorer cannot run from a clean clone.
- Commit identity: `Vishmayraj <zalavishmayraj@gmail.com>`. Small commits, one concern each, message style `feat:`, `fix:`, `docs:`, `test:`.
- Keep plain ASCII in docs and code comments. No em dashes.

## Decisions that need the owner

Each plan lists its own. The ones that block the most:

1. The alert budget for the threshold in plan 01: how many flags per 1,000 reports is acceptable to review.
2. Who reviews incidents, and whether one reviewer is enough to count as ground truth.
3. Where model artifacts live (plan 06): a MinIO bucket, GitHub release assets, or somewhere else.
4. Whether the marketing site keeps its illustrative mock data (plan 02).
