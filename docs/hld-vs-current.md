# HLD versus what exists now

A comparison of the design doc (`docs/HLD/HLD_and_Master_Implementation_Plan.md`) against the current-state documents in this directory. It compares documents only. It does not re-read the code, because `docs/architecture.md` and the per-area docs were written from the code and are treated as accurate. Where they say something is unverified or unmeasured, this doc repeats that instead of upgrading it.

Date of comparison: 2026-09-30. Status labels used below:

- **built**: exists and matches the HLD's intent
- **partial**: exists, but a piece the HLD names is missing or weaker
- **diverged**: exists in a different form from the HLD (not necessarily worse)
- **not built**: the HLD names it, nothing exists
- **not in HLD**: exists, the HLD never mentioned it

## Where the project is on the HLD's own calendar

The HLD Gantt starts Semester 5 on 2026-08-01 and runs 16 weeks. Read literally, today is about week 9.

| Gantt item | Planned window | State |
|---|---|---|
| Ingestion and normalization | Aug 1 to Aug 29 | done, running live |
| Research datasets and eval harness | Aug 29 to Sep 12 | done, but the harness is used only for a public smoke-test file |
| Bi-LSTM v1 | Sep 12 to Oct 10 | done, ahead of plan (F1 0.424, not the 0.9+ the HLD targeted) |
| Investigation agent v1 | Oct 10 to Oct 31 | done, ahead of plan, plus a Laya tool the HLD never had |
| Dashboard and Stage 1 writeup | Oct 31 to Nov 21 | not started; this is the remaining Semester 5 deliverable |

So the detection and agent layers are ahead of the calendar and the delivery layer is entirely missing. That gap is the largest single fact in this comparison.

## Layer by layer

### Ingestion and storage

| HLD says | Current docs say | Status |
|---|---|---|
| Collector consuming a public AIS feed | AISStream websocket, reconnect with backoff, running | built |
| `pyais` decoding AIVDM/NMEA | AISStream delivers JSON; the normalizer reshapes JSON. No NMEA decoding, `pyais` unused | diverged (reasonable) |
| Normalized records land in TimescaleDB | yes, `vessel_position`, `vessel_static` | built |
| Raw archive in object storage, for reprocessing | MinIO ndjson archive, write-only, nothing reads it back | partial |
| Dataset loader for research datasets | one loader for `gps_spoofing_mass` (smoke test). A separate MarineCadastre importer script exists | diverged |
| Feature store | none; windows are computed from Postgres on every run | not built (may not be needed) |
| PostGIS spatial queries | geography column and GiST index exist; the ML code reads plain lat/lon | built, lightly used |
| (implicit) clean data | no dedupe, no coordinate range checks, AIS sentinel values stored as reported, importer not idempotent, no migrations | partial |
| Kafka or Redpanda (Stage 2) | not built | out of stage |

### Detection layer

| HLD says | Current docs say | Status |
|---|---|---|
| Bi-LSTM predicts next state, deviation beyond physical limits is the signal | `BiLSTMNextDelta` predicts a lat/lon delta. Bidirectional over the whole window, so it can see the step it predicts. No vessel-class physical limits; one global threshold in degrees | diverged |
| Trained and validated against real-vs-simulated spoofing research and the IEEE dataset, reproduce 0.9+ | trained on 31M MarineCadastre rows. The IEEE file is only used by the harness smoke test. Offline F1 of 0.424 came from labels made by a synthetic injector, which has now been removed | diverged |
| Experiment tracking in MLflow | only offline scoring logs to MLflow; training does not | partial |
| Real-time scoring service | `scoring/live_scorer.py` polls every 30 s and calls the agent. No HTTP endpoint | partial |
| Physical-limits check per vessel class | proposed in comments, not built | not built |
| DBSCAN fleet-wide check (Stage 2) | `ml/models/clustering/` is a README. The landing site's mock data depicts a DBSCAN check as if it existed | not built (and misrepresented on the site) |
| Transformer model (Stage 2) | no directory, no code | out of stage |
| False-positive calibration (Stage 3) | threshold was tuned and reported on the same synthetic set. Live false positive rate is unmeasured | not built, and now the most urgent quality question |
| (not in HLD) rule detectors | freeze/replay and speed-jump detectors vote alongside the BiLSTM | not in HLD |

### Investigation layer

| HLD says | Current docs say | Status |
|---|---|---|
| Orchestrator in LangGraph or a bounded state machine | `investigate()` is a straight-line function. Six enum states are declared, three are never set | diverged |
| Tools: track history, jamming zones, incident history, vessel registry | first three exist. No registry tool (only the `vessel_static` join used as a model input) | partial |
| (not in HLD) Laya pattern classifier as a tool | exists, optional, fine-tuned once on injected data | not in HLD |
| Hypotheses: jamming, targeted spoof, equipment fault, benign | plus `freeze_replay` and `unresolved`. `benign` cannot be reached from the live scorer | partial |
| Never auto-declares spoofing; scored hypothesis with evidence | yes. Confidences are hand-set and uncalibrated | built, uncalibrated |
| Escalation driven by confidence, not anomaly magnitude | yes, threshold 0.7. But `escalated` is only a column value; no human ever acts on it | partial |
| Every tool call logged and auditable | `tool_call_log` JSONB with full outputs | built |
| Agent outputs feed back into zone DB and incident history | incident history yes (with a cross-vessel matching bug). Zone DB never written | partial |
| Claude Sonnet-class model for reasoning and reports | no LLM in the reasoning. Groq `openai/gpt-oss-120b` drafts report text only | diverged |
| Hand-curated jamming-zone list in Stage 1 | `jamming_zones` table exists, empty, nothing inserts. The `jamming` hypothesis cannot fire | not built |
| Incident history "currently small" | one recorded live incident (`ghast_latest_report.md`) | built, tiny |

Known agent behavior problems from `docs/agent.md` that matter for the HLD's principles: `incident_history` makes `targeted_spoof` reachable only for the first incident of each type string; `track_history` reads 24 h forward as well as back; `resolved` is never set, so any recent incident silences a vessel; Laya "agrees" with any non-normal label.

### Delivery layer

| HLD says | Current docs say | Status |
|---|---|---|
| FastAPI backend: scoring endpoint, incident feed, dashboard data | `backend/` holds a schema file and READMEs. No app | not built |
| React and MapLibre analyst dashboard: map, incident list, vessel drill-down | `frontend/dashboard/` is an empty directory with a README | not built |
| Incident and score feed for insurers | nothing | not built |
| (not in HLD) marketing site | static nginx site with scroll-scrubbed hero video, drawing from hardcoded mock data. The mock data shows DBSCAN checks, zone counts, meter deviations and auto-dismissal that do not exist | not in HLD |

Nothing downstream of the `incidents` table exists. The only ways to read an incident are SQL or the markdown stored in `report_text`.

### Infrastructure and process

| HLD says | Current docs say | Status |
|---|---|---|
| Docker and compose for Stages 1 and 2 | five services (timescaledb, minio, ingestion, scoring, frontend). No healthchecks. MinIO built from source | built |
| GitHub Actions CI | seven test workflows. No lint, no build, path filters miss some dependencies | built |
| Terraform, OAuth2/JWT, multi-tenant (Stage 3) | none | out of stage |
| Model artifacts reproducible | `epoch_010.pt` and the Laya weights exist only outside the repo. A fresh clone cannot run the scorer | not met |

### Data strategy

| HLD source | Current docs say | Status |
|---|---|---|
| Public live AIS | live ingestion running | built |
| IEEE DataPort synthetic GPS spoofing dataset | 25-row fixture plus a fetch script; used only to test the harness | partial |
| Real-vs-simulated spoofed-track studies | not used | not built |
| Public advisories and jamming zone reports | nothing ingests them; `data/jamming_zones/` has a README only | not built |
| Own incident history as a moat | one incident | built, empty |

### Risks table (HLD section 9)

| HLD risk and mitigation | Where it stands |
|---|---|
| False positives: confidence-driven escalation plus Stage 3 calibration | escalation exists but with hand-set confidences. The only measured false positive figure (19.4% of clean reports flagged) came from injected data and is not a live number |
| Agent becomes a chatbot: bounded tools, audit log, confidence escalation | bounded and audited, yes. The report text is LLM-written and nothing checks it against the evidence |
| Spoofing versus equipment degradation: explicit hypothesis space | space exists; `equipment_fault` is the fall-through, and the incident-history bug pushes real flags into it |
| No data access | solved for AIS. Still no labeled real spoofing, which is the harder half of the problem |
| CYTUR / incumbent, and AIS licensing | scheduled for Semester 6. `docs/research_notes/` holds `ml-data-strategy.md` only |

## Things that exist and the HLD never mentioned

- The Laya pattern classifier (export, fine-tune notebook, agent tool, benchmark).
- The `freeze_replay` hypothesis and corroboration from track history.
- Three-detector voting in the live scorer, with `--min-votes`.
- MarineCadastre historical import and the historical/live split by `message_type`.
- Groq report drafting.
- The marketing site.

## Document-level inconsistencies still open

`docs/architecture.md` already lists the stale READMEs (root, agent, ml, infra, backend and others). Not repeating them. Additional findings from this pass:

1. **The proposal doc is missing.** The HLD names `ais-gnss-threat-intel-proposal.md` as its companion and places it in `docs/` in its repo layout. It is not in the repo.
2. **The Laya notebook file changed.** The last commit replaced the upstream notebook with the GHAST-adapted one and renamed it `notebooks/laya_finetune_ghast_kaggle_2xT4.ipynb`. Docs written before that said the committed notebook was upstream's, unchanged. `docs/laya_pattern_classifier.md` has been corrected as part of the synthetic-data removal. Note that the notebook still reads `data/laya/train.jsonl` and `holdout.jsonl` from the GitHub clone, which no longer exist until a labeled export is built.
3. **HLD layout directories that do not exist:** `ml/models/transformer/`, `infra/terraform/`. `ml/models/clustering/` and `frontend/dashboard/` exist as README-only placeholders.
4. **The HLD's success target (0.9+ precision and recall) has no successor.** Nothing in the docs restates what "good enough" means now that the target dataset changed. The plans in `ImplementationPlans/` set one.

## What is missing, in the order it hurts

1. **No delivery layer.** No API, no dashboard, no way for anyone to see an incident. This is also the remaining Semester 5 deliverable.
2. **No trustworthy quality number.** The only measurement was against injected spoofs, and it is gone. Real traffic has no labels, so quality has to come from flag rates on real data now and analyst review later.
3. **The agent's escalation path ends in a column.** No reviewer, no `resolved`, no learning from outcomes, and several rule bugs.
4. **Jamming and fleet context is absent.** Empty zone table, no clustering. The agent cannot tell one vessel misbehaving from a whole area being jammed, which is the HLD's central distinction.
5. **Nothing is reproducible from a clone.** Weights and checkpoint live elsewhere; no migrations; importer not idempotent.
6. **Model design questions never ablated:** bidirectional context, missing time-step input, rate-of-turn never seen in training.

The plans in `ImplementationPlans/` (index in `ImplementationPlans/README.md`) are ordered against this list.
