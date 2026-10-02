"""What every incident agent is handed: one stored incident and the scorer's thresholds."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IncidentContext:
    incident_id: str
    mmsi: int
    flagged_at: Any
    latitude: float
    longitude: float
    score: float
    tier: str
    hypothesis: str
    confidence: float
    votes: tuple[str, ...]
    evidence: dict[str, Any]
    threshold_a: float | None = None
    threshold_b: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def card(self) -> dict[str, Any]:
        """The compact, JSON-safe summary agents see in their task (no raw track)."""
        pattern = self.evidence.get("pattern_classifier") or {}
        return {
            "incident_id": self.incident_id, "mmsi": self.mmsi, "flagged_at": str(self.flagged_at),
            "score": self.score, "tier": self.tier, "threshold_a": self.threshold_a, "threshold_b": self.threshold_b,
            "hypothesis": self.hypothesis, "confidence": self.confidence, "detector_votes": list(self.votes),
            "laya": {"available": pattern.get("available"), "pattern": pattern.get("pattern"), "confidence": pattern.get("confidence")},
            "freeze_corroborated": bool((self.evidence.get("freeze_corroboration") or {}).get("matched")),
            "zone_matched": bool((self.evidence.get("jamming_zones") or {}).get("matched")),
        }
