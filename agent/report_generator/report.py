"""Groq-backed report draft boundary."""
from __future__ import annotations
import json
import os
from typing import Any

# Keep the deployment choice in one environment-driven setting. Groq's client reads
# GROQ_API_KEY itself, so no credential is passed through this module or source code.
DEFAULT_REPORT_MODEL = "openai/gpt-oss-120b"
DEFAULT_REPORT_MAX_TOKENS = 1200

REPORT_PROMPT_PREFIX = """You are GHAST's incident-brief writer for maritime AIS anomaly investigations.
Create a compact, decision-ready Markdown brief from ONLY the supplied incident summary.
Do not invent facts, thresholds, regulations, vessel identity, intent, or recommendations.
Do not repeat the full track history. Use a calm analyst tone and preserve uncertainty.

Use exactly this visual structure:

# GHAST // INCIDENT BRIEF
> `HYPOTHESIS` · `CONFIDENCE%` · `STATUS`

**MMSI:** ...  · **Flagged:** ...  · **Score:** ...

```mermaid
flowchart LR
    A["actual detector votes"] --> B["actual evidence counts"]
    B --> C["actual GHAST hypothesis"]
    C --> D["exact database status"]
```

Replace every quoted graph label with the actual supplied values. Do not use
`OPEN`, `CLOSED`, or another status that is not present in the input; GHAST status
values are `reported`, `escalated`, or `resolved`.

## Signal
One or two sentences explaining what GHAST observed. Cite only supplied evidence.

## Evidence
- detector corroboration
- freeze/jamming/history findings
- bounded track summary (count and endpoints only)

## Analyst action
Give at most three proportionate follow-up actions. If the evidence is insufficient,
say so instead of escalating the claim.

Keep the complete response under 700 words. Return Markdown only.

Incident summary:
"""

def report_model() -> str:
    return os.environ.get("GHAST_REPORT_MODEL") or DEFAULT_REPORT_MODEL

def _report_max_tokens() -> int:
    raw = os.environ.get("GHAST_REPORT_MAX_TOKENS")
    return int(raw) if raw else DEFAULT_REPORT_MAX_TOKENS


def _compact_report_input(incident: dict[str, Any]) -> dict[str, Any]:
    """Keep large trajectory evidence out of the LLM request.

    The persisted incident remains the complete audit record. Reports only need the
    decision fields and bounded evidence summaries; sending every AIS position can
    exceed Groq's organization TPM limit before output tokens are considered.
    """
    evidence = incident.get("evidence") or {}
    track_history = evidence.get("track_history") or {}
    positions = track_history.get("positions") or []
    compact_evidence = {
        "track_history": {
            "position_count": len(positions),
            "first_position": positions[0] if positions else None,
            "last_position": positions[-1] if positions else None,
        },
        "jamming_zones": evidence.get("jamming_zones"),
        "incident_history": evidence.get("incident_history"),
        "freeze_corroboration": evidence.get("freeze_corroboration"),
        "detector_corroboration": evidence.get("detector_corroboration"),
    }
    return {
        key: incident.get(key)
        for key in ("mmsi", "flagged_at", "anomaly_score", "anomaly_type", "hypothesis", "confidence", "status")
    } | {"evidence": compact_evidence}


async def draft_report(incident: dict[str, Any], client: Any) -> str:
    """Only reporting calls an LLM; all upstream classification is deterministic and auditable."""
    report_input = _compact_report_input(incident)
    completion = await client.chat.completions.create(
        model=report_model(),
        max_tokens=_report_max_tokens(),
        messages=[
            {
                "role": "user",
                "content": REPORT_PROMPT_PREFIX
                + json.dumps(report_input, default=str, separators=(",", ":")),
            }
        ],
    )
    # A provider can legitimately return no textual content (for example, a refused
    # response). Persist an empty report rather than raising after the investigation
    # has already gathered auditable evidence.
    return completion.choices[0].message.content or ""
