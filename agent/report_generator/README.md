# agent/report_generator/

Drafts the structured incident report once the orchestrator reaches a high-confidence
hypothesis: vessel, time, anomaly type, score, supporting evidence from each tool,
and the hypothesis itself. It uses Groq's async client, which obtains its credential
from `GROQ_API_KEY`. Its default model is `openai/gpt-oss-120b`; override it with
`GHAST_REPORT_MODEL`.
