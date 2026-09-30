# agent/

The investigation layer: a bounded, tool-using agent that turns a flagged anomaly into an evidence-backed incident report. This is the "agentic" requirement from the proposal — bounded, evidence-gathering security work, not open-ended chat.

- `orchestrator/` — the state machine driving the agent workflow.
- `tools/` — track history, jamming-zone lookup, incident history, and the optional Laya `pattern_classifier`.
- `report_generator/` — drafts the structured incident report.

Design principles (carried over from the proposal, MIP section 4.2): the agent never auto-declares "this is spoofing" — it produces a scored hypothesis with evidence attached, escalation is confidence-driven rather than magnitude-driven, and every tool call is logged so reports stay auditable.

## Test the bounded workflow

From `C:\Projects\GHAST\agent`, install the test dependencies and run the
mock-only suite. It makes no database or Groq API request:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -v
```

## Manual integration test

Only run this after the training data gate in `ml/training/README.md` has been
met and a real scoring caller can supply a flagged anomaly. Before doing so,
verify the schema and inspect the audit result with:

```powershell
cd C:\Projects\GHAST\infra\docker
docker compose exec timescaledb psql -U ghast -d ghast -c "SELECT id, mmsi, flagged_at, hypothesis, confidence, status, jsonb_array_length(tool_call_log) AS audit_entries, report_text IS NOT NULL AS has_report FROM incidents ORDER BY created_at DESC LIMIT 10;"
```

An acceptable real run has either `status = 'escalated'` or a non-empty
`report_text`, and three evidence tool calls in `audit_entries` (four when the `pattern_classifier` tool is registered, as `scoring/live_scorer.py` always does).
Never put the Groq credential in source control; inject `GROQ_API_KEY` only through
the runtime environment used by the report-drafting caller. The default report model
is `openai/gpt-oss-120b`; set `GHAST_REPORT_MODEL` to override it.
