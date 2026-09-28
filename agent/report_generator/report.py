"""Claude-backed report draft boundary."""
from __future__ import annotations
import os
from typing import Any

# Read from the environment rather than hardcoded: Anthropic ships new model IDs
# regularly and a dated snapshot string here silently goes stale (it did - this used
# to be "claude-sonnet-4-20250514"). Default verified against the Claude Platform
# models overview: Sonnet 5's API ID is `claude-sonnet-5` (dateless IDs from the 4.6
# generation on are pinned snapshots, not evergreen pointers, so revisit this default
# when a newer Sonnet ships - or just set GHAST_REPORT_MODEL and skip the code change).
DEFAULT_REPORT_MODEL = "claude-sonnet-5"
# Sonnet 5 uses adaptive thinking by default and thinking tokens count against
# max_tokens, so the old 600 could be consumed before any report text is emitted.
DEFAULT_REPORT_MAX_TOKENS = 2000

def report_model() -> str:
    return os.environ.get("GHAST_REPORT_MODEL") or DEFAULT_REPORT_MODEL

def _report_max_tokens() -> int:
    raw = os.environ.get("GHAST_REPORT_MAX_TOKENS")
    return int(raw) if raw else DEFAULT_REPORT_MAX_TOKENS

async def draft_report(incident: dict[str, Any], client: Any) -> str:
    """Only reporting calls an LLM; all upstream classification is deterministic and auditable."""
    message = await client.messages.create(model=report_model(), max_tokens=_report_max_tokens(), messages=[{"role": "user", "content": f"Draft a concise AIS incident report from: {incident}"}])
    # content[0] may be a thinking block on current models; take the text blocks.
    return "".join(block.text for block in message.content if getattr(block, "type", None) == "text")
