"""Groq-backed report draft boundary."""
from __future__ import annotations
import os
from typing import Any

# Keep the deployment choice in one environment-driven setting. Groq's client reads
# GROQ_API_KEY itself, so no credential is passed through this module or source code.
DEFAULT_REPORT_MODEL = "openai/gpt-oss-120b"
DEFAULT_REPORT_MAX_TOKENS = 2000

def report_model() -> str:
    return os.environ.get("GHAST_REPORT_MODEL") or DEFAULT_REPORT_MODEL

def _report_max_tokens() -> int:
    raw = os.environ.get("GHAST_REPORT_MAX_TOKENS")
    return int(raw) if raw else DEFAULT_REPORT_MAX_TOKENS

async def draft_report(incident: dict[str, Any], client: Any) -> str:
    """Only reporting calls an LLM; all upstream classification is deterministic and auditable."""
    completion = await client.chat.completions.create(
        model=report_model(),
        max_tokens=_report_max_tokens(),
        messages=[{"role": "user", "content": f"Draft a concise AIS incident report from: {incident}"}],
    )
    # A provider can legitimately return no textual content (for example, a refused
    # response). Persist an empty report rather than raising after the investigation
    # has already gathered auditable evidence.
    return completion.choices[0].message.content or ""
