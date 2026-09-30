# docs/

The design doc (`HLD_and_Master_Implementation_Plan.md`) lives in `HLD/` in this directory and stays the single source of truth for design intent. It describes what GHAST is meant to become; the documents below describe what the code does now.

Start with `architecture.md`. It links to the rest:

- `architecture.md`: end-to-end flow, component status, known problems
- `data-pipeline.md`: ingestion, historical import, schema
- `infrastructure.md`: compose stack, environment variables, local requirements
- `ml-pipeline.md`: features, BiLSTM, training, injectors
- `scoring-and-evaluation.md`: offline evaluation, threshold, live scorer
- `agent.md`: investigation agent
- `laya_pattern_classifier.md`: Laya export, model, use in the agent
- `backend-and-frontend.md`: what exists (little)
- `testing.md`: test map and CI
- `DEVELOPER_GUIDE.md`: older recovery guide

`research_notes/` holds consensus/red-team findings as they come in (MIP section 9 risks).
