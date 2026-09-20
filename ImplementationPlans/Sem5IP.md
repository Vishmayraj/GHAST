# Semester 5 Implementation Plan (Stage 1)

Scope: Section 1 / Stage 1 only, as defined in `HLD/HLD_and_Master_Implementation_Plan.md` (sections 3, 4, 5, 7, 8.2). This document exists so we can pick up any week and know exactly what to build, in what order, and what "done" looks like, without re-reading the full MIP each time.

Everything here is Stage 1 scope only. Stage 2 items (transformer migration, DBSCAN automation, campaign correlation, insurer outreach) and Stage 3 items (multi-tenant auth, Terraform, pilot integration) are explicitly out of scope for this document and are not being scaffolded yet.

---

## 1. Goal of Semester 5

Ship a complete, demoable Stage 1 system:

- A pipeline that continuously ingests real, live AIS vessel tracks and lands clean records in a spatio-temporal store.
- A reproducible evaluation harness (precision/recall/F1) benchmarked against published baselines.
- A Bi-LSTM trajectory anomaly model that reproduces credible published-level detection performance, tracked in MLflow.
- A rule-assisted investigation agent with three tools (track history, jamming-zone lookup, incident history) that turns a flagged anomaly into an auditable, evidence-backed incident report.
- A minimal dashboard (map view, incident list, per-vessel drill-down).
- A packaged Stage 1 report: architecture, benchmark results, sample incident reports, business case.

This is the professor-facing deliverable for Semester 5, and it is also the foundation Stage 2 builds directly on top of.

---

## 2. What we are building this stage (component scope)

Only the pieces below are in scope for Stage 1. Anything in the full MIP repo layout (section 7) not listed here is Stage 2/3 and stays out of the tree until its stage arrives.

| Layer | Stage 1 scope | Explicitly NOT this stage |
|---|---|---|
| Ingestion | Live AIS collector (public feed), AIVDM/NMEA normalizer | Kafka/Redpanda queue (Stage 2+ volume) |
| Storage | TimescaleDB (Postgres + PostGIS) for tracks, object storage for raw archive | — |
| ML | Feature extraction, Bi-LSTM model, training pipeline, evaluation harness, MLflow tracking | Transformer model, fleet-wide DBSCAN clustering (Stage 2) |
| Agent | Orchestrator (state machine), 3 tools (track history, jamming-zone DB, incident history), report generator | Automated jamming-zone ingestion, campaign correlation (Stage 2) |
| Backend | FastAPI: scoring endpoint, incident feed, dashboard data | OAuth2/JWT, per-customer API keys, rate limiting (Stage 3) |
| Frontend | React + map layer: map view, incident list, vessel drill-down | — |
| Infra | Docker + docker-compose for local dev, GitHub Actions CI skeleton | Terraform (Stage 3) |

---

## 3. Detailed weekly milestones (from MIP section 8.2)

### Weeks 1-4: Ingestion and normalization
- Stand up the AIS collector against a public feed (terrestrial and/or satellite).
- Build the AIVDM/NMEA normalizer (`pyais`) and land clean, schema-normalized records into TimescaleDB.
- Archive raw messages to S3-compatible object storage for reprocessing/retraining later.
- **Deliverable:** a running pipeline continuously ingesting real, live vessel tracks.

### Weeks 5-6: Evaluation harness
- Load the IEEE DataPort synthetic GPS spoofing dataset.
- Load the real-vs-simulated spoofed AIS track research data.
- Build the evaluation harness (precision, recall, F1) so every model iteration from here on has a number attached.
- **Deliverable:** a reproducible benchmark script anyone on the team can run.

### Weeks 7-10: Detection model v1
- Extract features (speed, course, rate of turn, vessel class) from raw tracks.
- Train the Bi-LSTM trajectory model (next-state prediction; large prediction-vs-physical-limits deviation is the spoofing signal).
- Benchmark against published baselines from the literature; target 0.9+ precision/recall to be credible.
- Track every run in MLflow.
- **Deliverable:** a model that reproduces credible published-level detection performance, tracked in MLflow.

### Weeks 11-13: Investigation agent v1
- Build the orchestrator (LangGraph, or a custom bounded state machine if LangGraph is heavier than needed) with three tools:
  - `track_history` — pull recent + full trajectory for a vessel.
  - `jamming_zones` — check a hand-curated list of known jamming/spoofing zones.
  - `incident_history` — check for similar past incidents (small at this stage; grows over time).
- Wire the agent to form a hypothesis (jamming / targeted spoof / equipment fault / benign) and, on high confidence, generate a structured incident report. On low confidence or a novel pattern, escalate to a human analyst instead of guessing.
- Log every agent action (tool called, what it found) so reports are auditable, not a black box.
- **Deliverable:** an end-to-end flow from raw anomaly to a readable, evidence-backed incident report.

### Weeks 14-16: Dashboard and Stage 1 writeup
- Build the minimal dashboard: map view, incident list, drill-down per vessel.
- Assemble the Stage 1 report: architecture, benchmark results, sample incident reports, and the proposal's business case, packaged as the Semester 5 deliverable.
- **Deliverable:** a complete, demoable Semester 5 project satisfying every one of the professor's Stage 1 requirements.

---

## 4. Data strategy for Stage 1 (from MIP section 5)

No vessel access, insurer data, or proprietary industry relationship is required to reach a working Stage 1 — everything below is public.

| Source | What it gives us | Access pattern |
|---|---|---|
| Public terrestrial/satellite AIS feeds | Real, live vessel tracks | Free public feeds |
| IEEE DataPort synthetic GPS spoofing dataset | Labeled ground truth for training/eval | Public research dataset |
| Real-vs-simulated spoofed AIS track studies | Additional labeled/benchmarked cases | Public research literature |
| Public maritime advisories, known jamming zone reports | Seed data for the jamming-zone DB | Manually curated at this stage |
| Our own accumulating incident history | Growing proprietary dataset over time | Generated by the system itself once running |

---

## 5. Tech stack for Stage 1 (trimmed from MIP section 6)

| Layer | Choice |
|---|---|
| Ingestion | Python service, `pyais` for AIVDM/NMEA decoding |
| Storage | TimescaleDB (Postgres + PostGIS) |
| Raw archive | S3-compatible object storage (MinIO locally) |
| ML framework | PyTorch |
| Classical ML | scikit-learn |
| Experiment tracking | MLflow |
| Agent orchestration | LangGraph, or a custom bounded state machine |
| LLM for agent reasoning | Claude (Sonnet-class) via API |
| Backend API | FastAPI (Python) |
| Frontend dashboard | React + MapLibre GL or Deck.gl |
| CI/CD | GitHub Actions |
| Containerization | Docker + docker-compose |

---

## 6. Repo structure being scaffolded this stage

```
GHAST/
  ImplementationPlans/
    Sem5IP.md               # this document
  data/
    raw/                    # archived raw AIS messages (gitignored, points to object storage)
    research_datasets/      # IEEE DataPort + published labeled datasets
    jamming_zones/          # curated known jamming/spoofing zone data
  ingestion/
    collector/              # live AIS feed consumer
    normalizer/             # AIVDM/NMEA decoding, schema normalization
  ml/
    features/               # feature extraction pipeline
    models/
      bilstm/                # Stage 1 model
      clustering/            # scaffolded now, wired up in Stage 2
    training/                # training scripts, MLflow configs
    evaluation/              # benchmark scripts against published baselines
  agent/
    orchestrator/            # LangGraph / state machine definition
    tools/                   # track_history.py, jamming_zones.py, incident_history.py
    report_generator/        # structured incident report drafting
  backend/
    api/                     # FastAPI app: scoring endpoint, incident feed, dashboard data
    models/                  # DB schema, ORM models
  frontend/
    dashboard/               # React app: map view, incident list, vessel drill-down
  infra/
    docker/                  # Dockerfiles, docker-compose.yml
    ci/                      # GitHub Actions workflows
  docs/
    HLD_and_Master_Implementation_Plan.md
    research_notes/          # consensus/red-team findings as they come in
  notebooks/
    exploration/             # data exploration, model prototyping
```

`infra/terraform/` and multi-tenant auth are Stage 3 and are intentionally not created yet, per section 2 above.

---

## 7. Definition of done for Semester 5

- [ ] AIS collector running continuously against a public feed, normalizer landing clean records in TimescaleDB.
- [ ] Evaluation harness runnable end-to-end, producing precision/recall/F1 against published baselines.
- [ ] Bi-LSTM model trained, benchmarked, tracked in MLflow, reproducing credible published-level performance.
- [ ] Agent orchestrator with all 3 tools wired, producing auditable structured incident reports with confidence-driven escalation.
- [ ] Dashboard showing map view, incident list, and per-vessel drill-down.
- [ ] Stage 1 report assembled (architecture + benchmarks + sample incident reports + business case) and ready to present.

---

## 8. Immediate next steps (post-scaffold)

1. Kick off Week 1: stand up the AIS collector against a chosen public feed.
2. Optionally run the consensus/red-team prompt from the proposal's appendix in parallel, to close the CYTUR and AIS-data-licensing questions early rather than waiting for Semester 6 (per MIP section 10).
