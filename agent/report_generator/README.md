# agent/report_generator/

Drafts the structured incident report (`on_demand.py`; the orchestrator never calls it):
automatically for tier B incidents, and when an analyst asks for one on tier A. Every draft
is verified against the stored incident and expires after 24 hours (the incident stays).
Contents: vessel, time, anomaly type, score, supporting evidence from each tool,
and the hypothesis itself. It uses Groq's async client, which obtains its credential
from `GROQ_API_KEY`. Its default model is `openai/gpt-oss-120b`; override it with
`GHAST_REPORT_MODEL`. Reports use a compact GHAST incident-brief format with one
small Mermaid signal-flow diagram; full trajectory evidence remains in PostgreSQL,
not in the LLM prompt. The output budget defaults to 1200 tokens and can be changed
with `GHAST_REPORT_MAX_TOKENS`.
