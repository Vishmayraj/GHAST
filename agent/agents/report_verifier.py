"""Report verifier agent: checks a drafted report against the stored incident.

Closes an HLD risk (docs/hld-vs-current.md): "the report text is LLM-written and nothing checks
it against the evidence". The verifier is handed the draft and the authoritative incident
facts. It looks for claims the record does not support. A deterministic checker backs it up in
code: whatever the model says, a draft that fails those checks cannot pass.
"""
from __future__ import annotations

import re
from typing import Any

from runtime.agent import Agent, ToolSpec

FORBIDDEN_STATUS_WORDS = ("OPEN", "CLOSED")  # GHAST statuses are reported, escalated, resolved
CONFIDENCE_TOLERANCE_POINTS = 1

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "issues": {"type": "array", "items": {"type": "string"}, "description": "Each unsupported or wrong claim, briefly. Empty when the verdict is pass."},
    },
    "required": ["verdict", "issues"],
}

SYSTEM_PROMPT = """You are GHAST's report verifier. You are given a drafted incident brief and the authoritative incident record.
Find every claim in the brief the record does not support: wrong MMSI, hypothesis, confidence or status; invented vessel identity, intent, regulations or thresholds; a statement that spoofing is confirmed when it is only a hypothesis.
Call incident_facts and draft_report, compare, then finish with verdict pass (no issues) or fail (list each issue). Be strict about facts, not style."""


def check_report(facts: dict[str, Any], text: str) -> list[str]:
    """Deterministic checks that need no model. Returns a list of problems (empty = clean)."""
    issues: list[str] = []
    if not text or not text.strip():
        return ["the report is empty"]
    if str(facts["mmsi"]) not in text:
        issues.append(f"the MMSI {facts['mmsi']} does not appear")
    hypothesis = str(facts["hypothesis"])
    if hypothesis not in text and hypothesis.replace("_", " ") not in text:
        issues.append(f"the hypothesis '{hypothesis}' is never named")
    for word in FORBIDDEN_STATUS_WORDS:
        if re.search(rf"\b{word}\b", text):
            issues.append(f"uses the status word {word}, which GHAST does not have")
    percent = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", text)
    if percent and facts.get("confidence") is not None:
        shown, actual = float(percent.group(1)), float(facts["confidence"]) * 100
        if abs(shown - actual) > CONFIDENCE_TOLERANCE_POINTS:
            issues.append(f"states {shown:g}% but the stored confidence is {actual:.0f}%")
    if re.search(r"confirmed\s+(?:gnss\s+|gps\s+|ais\s+)?spoof", text, re.I) and facts.get("review_verdict") != "confirmed_spoof":
        issues.append("says spoofing is confirmed, but no analyst has confirmed it")
    return issues


def validate_factory(facts: dict[str, Any], text: str):
    def validate(decision: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
        verdict = decision.get("verdict")
        if verdict not in ("pass", "fail"):
            raise ValueError("verdict must be pass or fail")
        issues = [str(i)[:300] for i in (decision.get("issues") or [])]
        if verdict == "fail" and not issues:
            raise ValueError("a fail needs at least one issue")
        # The code-side check is authoritative: a model cannot pass a draft these checks reject.
        rule_issues = check_report(facts, text)
        if rule_issues:
            return {"verdict": "fail", "issues": list(dict.fromkeys(rule_issues + issues))}
        return {"verdict": verdict, "issues": issues if verdict == "fail" else []}
    return validate


def baseline_factory(facts: dict[str, Any], text: str):
    def baseline(task: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
        issues = check_report(facts, text)
        return {"verdict": "fail" if issues else "pass", "issues": issues}
    return baseline


def build_report_verifier(facts: dict[str, Any], text: str, client: Any, model: str | None) -> Agent:
    async def incident_facts(_: dict) -> dict:
        return facts

    async def draft_report(_: dict) -> dict:
        return {"text": text}

    none = {"type": "object", "properties": {}}
    return Agent(
        "report_verifier_agent", SYSTEM_PROMPT,
        [ToolSpec("incident_facts", "The authoritative incident record the brief must agree with.", none, incident_facts),
         ToolSpec("draft_report", "The drafted brief to check.", none, draft_report)],
        DECISION_SCHEMA, validate_factory(facts, text), baseline_factory(facts, text), client=client, model=model,
    )
