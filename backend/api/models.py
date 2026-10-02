"""Response and request models: the typed contract the console reads."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Hypothesis = Literal["jamming", "targeted_spoof", "freeze_replay", "equipment_fault", "benign", "unresolved"]
Status = Literal["reported", "escalated", "resolved"]
Verdict = Literal["confirmed_spoof", "jamming", "equipment_fault", "benign", "unclear"]


class HealthOut(BaseModel):
    database: str
    latest_position_age_s: float | None
    latest_incident_age_s: float | None


class IncidentSummary(BaseModel):
    id: str
    mmsi: int
    vessel_name: str | None = None
    flagged_at: datetime
    hypothesis: Hypothesis
    confidence: float | None
    status: Status
    tier: Literal["A", "B"]
    priority: float | None = None
    anomaly_type: str | None = None
    review_verdict: Verdict | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class IncidentPage(BaseModel):
    items: list[IncidentSummary]
    next_before: datetime | None = None


class IncidentDetail(IncidentSummary):
    window_start: datetime | None = None
    window_end: datetime | None = None
    anomaly_score: float
    votes: list[dict[str, Any]]
    evidence: list[dict[str, str]]
    fleet_context: dict[str, Any] | None = None
    challenge: dict[str, Any] | None = None
    tool_call_log: list[dict[str, str]]
    report_text: str | None = None
    report_expires_at: datetime | None = None
    report_verification: dict[str, Any] | None = None
    review_notes: str | None = None
    track: list[dict[str, Any]]


class TrackOut(BaseModel):
    mmsi: int
    points: list[dict[str, Any]]


class ReviewIn(BaseModel):
    verdict: Verdict
    notes: str | None = Field(default=None, max_length=2000)
    reviewer: str = Field(default="analyst", min_length=1, max_length=80)


class ReviewStatsOut(BaseModel):
    reviewed: int
    unclear: int
    minimum: int
    rows: list[dict[str, Any]]
    by_votes: list[dict[str, Any]]


class ThresholdsOut(BaseModel):
    active: dict[str, Any] | None
    history: list[dict[str, Any]]
