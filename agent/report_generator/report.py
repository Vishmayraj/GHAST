"""Claude-backed report draft boundary."""
from __future__ import annotations
import os
from typing import Any
async def draft_report(incident: dict[str, Any], client: Any) -> str:
    """Only reporting calls an LLM; all upstream classification is deterministic and auditable."""
    message = await client.messages.create(model="claude-sonnet-4-20250514", max_tokens=600, messages=[{"role": "user", "content": f"Draft a concise AIS incident report from: {incident}"}])
    return message.content[0].text
