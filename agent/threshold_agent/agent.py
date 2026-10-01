"""The threshold agent: keeps alert thresholds A and B right as the model changes.

Why an agent and not a constant: both thresholds are properties of one specific model. Every
time the BiLSTM is retrained or Laya is fine-tuned (weekly, or whenever the pipeline runs) the
score scale shifts, and a stale number floods the console or goes silent. So something has to
look at the evidence and decide, with reasons that can be audited.

    A  a flag at or above it becomes an incident on the console (report on a button press)
    B  (> A) a flag at or above it is also drafted automatically and listed first

Evidence the agent can pull (read-only tools): the active thresholds, the score distribution
the scorer recorded for the CURRENT model version, the flag rate each candidate threshold
would give, offline score_checkpoint reports, incident volume, and analyst verdicts by tier.
It commits through `finish`; code validates the decision (hard bounds below) and only then does
`apply_decision` write a new `threshold_config` row. Without an LLM key the deterministic
`budget_policy` makes the same call from the same evidence.

Alert budgets (the share of scored reports allowed to flag) are the owner's decision
(ImplementationPlans/README.md, decision 1). The defaults below are placeholders.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from features.score_histogram import merge_counts, share_above, threshold_for_share
from runtime.agent import Agent, ToolSpec, record_run

logger = logging.getLogger(__name__)

# Uncalibrated placeholders for the owner's alert budget: share of scoreable reports flagged.
DEFAULT_BUDGET_A = 1e-4  # about 1 in 10,000 reports reaches the console
DEFAULT_BUDGET_B = 1e-5  # about 1 in 100,000 is drafted automatically

# Hard bounds, enforced in code whatever the model says.
MIN_REPORTS_TO_RETUNE = 50_000        # fewer scored reports on this model version: keep what we have
MAX_STEP_FACTOR = 3.0                 # per decision, A may move at most 3x either way ...
ABSOLUTE_MIN, ABSOLUTE_MAX = 1e-4, 10.0  # ... and never leave this range (degrees)
MIN_B_OVER_A = 1.5                    # B must sit clearly above A
HYSTERESIS = 0.25                     # ignore moves smaller than 25 percent in A (no flapping)
STATS_HOURS = 72

CURRENT_QUERY = """
SELECT threshold_a, threshold_b, model_version, set_by, reason, created_at
FROM threshold_config ORDER BY id DESC LIMIT 1
"""
STATS_QUERY = """
SELECT reports_scored, skipped_unscorable, flagged_a, flagged_b, histogram
FROM scoring_stats
WHERE scored_at > now() - make_interval(hours => $1::int) AND model_version IS NOT DISTINCT FROM $2
"""
VOLUME_QUERY = """
SELECT tier, count(*) AS n FROM incidents
WHERE created_at > now() - interval '24 hours' GROUP BY tier
"""
REVIEWS_QUERY = """
SELECT tier, review_verdict, count(*) AS n FROM incidents
WHERE review_verdict IS NOT NULL GROUP BY tier, review_verdict
"""
INSERT_QUERY = """
INSERT INTO threshold_config (threshold_a, threshold_b, model_version, set_by, reason)
VALUES ($1, $2, $3, $4, $5) RETURNING id
"""


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes)) else value


@dataclass(frozen=True)
class Budgets:
    a: float = DEFAULT_BUDGET_A
    b: float = DEFAULT_BUDGET_B


async def get_active(db: Any) -> dict[str, Any] | None:
    row = await db.fetchrow(CURRENT_QUERY)
    return dict(row) if row else None


async def _merged_histogram(db: Any, model_version: str | None, hours: int = STATS_HOURS) -> tuple[list[int], int, int]:
    rows = await db.fetch(STATS_QUERY, hours, model_version)
    if not rows:
        return [], 0, 0
    histograms = [_json(r["histogram"]) for r in rows]
    return merge_counts(histograms), sum(r["reports_scored"] for r in rows), sum(r["skipped_unscorable"] for r in rows)


def build_tools(db: Any, model_version: str | None, reports_dir: Path | None) -> list[ToolSpec]:
    async def current_thresholds(_: dict) -> dict:
        active = await get_active(db)
        return {"active": active, "scorer_model_version": model_version,
                "model_changed_since_set": bool(active and active.get("model_version") != model_version)}

    async def score_distribution(args: dict) -> dict:
        hours = int(args.get("hours", STATS_HOURS))
        counts, scored, skipped = await _merged_histogram(db, model_version, hours)
        if not counts:
            return {"reports_scored": 0, "note": "no scoring stats recorded for the current model version"}
        return {"hours": hours, "reports_scored": scored, "skipped_unscorable": skipped,
                "score_at_flag_share": {f"{s:g}": threshold_for_share(counts, s) for s in (1e-2, 1e-3, 1e-4, 1e-5, 1e-6)}}

    async def flag_rate_at(args: dict) -> dict:
        counts, scored, _ = await _merged_histogram(db, model_version)
        if not counts:
            return {"reports_scored": 0}
        threshold = float(args["threshold"])
        share = share_above(counts, threshold)
        return {"threshold": threshold, "flag_share": share, "reports_scored": scored, "expected_flags_in_window": share * scored}

    async def incident_volume(_: dict) -> dict:
        rows = await db.fetch(VOLUME_QUERY)
        return {"incidents_last_24h": {r["tier"]: r["n"] for r in rows}}

    async def analyst_verdicts(_: dict) -> dict:
        rows = await db.fetch(REVIEWS_QUERY)
        by_tier: dict[str, dict[str, int]] = {}
        for r in rows:
            by_tier.setdefault(r["tier"], {})[r["review_verdict"]] = r["n"]
        total = sum(sum(v.values()) for v in by_tier.values())
        return {"reviewed_total": total, "by_tier": by_tier,
                "note": "too few reviews to trust" if total < 30 else "enough reviews to read per-tier precision"}

    async def offline_reports(_: dict) -> dict:
        found = []
        for path in sorted(reports_dir.glob("*.json")) if reports_dir and reports_dir.is_dir() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if "threshold_for_flag_rate" in data:
                found.append({"file": path.name, "source": (data.get("eval") or {}).get("source"),
                              "checkpoint": data.get("checkpoint"), "reports": data.get("reports"),
                              "threshold_for_flag_rate": data["threshold_for_flag_rate"]})
        return {"reports": found, "note": "offline reports may predate the data-quality gate"}

    none = {"type": "object", "properties": {}}
    return [
        ToolSpec("current_thresholds", "Active thresholds A and B, who set them, and whether the model changed since.", none, current_thresholds),
        ToolSpec("score_distribution", "Prediction-error score at several flag shares, from live scoring on the current model.",
                 {"type": "object", "properties": {"hours": {"type": "integer"}}}, score_distribution),
        ToolSpec("flag_rate_at", "Share of live reports that would flag at a candidate threshold (degrees).",
                 {"type": "object", "properties": {"threshold": {"type": "number"}}, "required": ["threshold"]}, flag_rate_at),
        ToolSpec("incident_volume", "Incidents created in the last 24 hours, by tier.", none, incident_volume),
        ToolSpec("analyst_verdicts", "Analyst verdict counts by tier, with a too-few-to-trust note.", none, analyst_verdicts),
        ToolSpec("offline_reports", "score_checkpoint reports on disk with the threshold for several flag rates.", none, offline_reports),
    ]


DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["keep", "update"]},
        "threshold_a": {"type": "number", "description": "Degrees. Required when action is update."},
        "threshold_b": {"type": "number", "description": "Degrees, above A. Required when action is update."},
        "reason": {"type": "string", "description": "Two sentences max, citing the numbers you used."},
    },
    "required": ["action", "reason"],
}

SYSTEM_PROMPT = """You are GHAST's threshold agent. GHAST scores AIS position reports with a model and flags the ones whose prediction error is unusually large.
Threshold A: reports at or above it become incidents on the analyst console. Threshold B (higher): also get a report drafted automatically and are listed first.
Both depend on the current model; they must be re-derived whenever the model changes.

Your job: decide whether to keep or update A and B. Use your tools to look at the active thresholds, the live score distribution, the flag rate at candidates, incident volume and analyst verdicts.
Aim for the alert budget given in the task (the share of scoreable reports that may flag at A and at B). Prefer score_distribution values near those shares, then check them with flag_rate_at.
Rules you cannot break (they are enforced): at least 50000 scored reports on the current model version before changing anything, unless you adopt a value from an offline report; on the same model A moves at most 3x per decision (a new model may rescale freely); B must be at least 1.5x A; ignore changes smaller than 25 percent.
If the evidence is thin or the active thresholds already meet the budget, keep them. Never invent numbers: use only what the tools returned. Call finish once."""


def _from_offline_report(a: float, observations: dict[str, Any]) -> bool:
    """True when `a` equals (within 1 percent) a threshold an offline score_checkpoint report gave."""
    for report in (observations.get("offline_reports") or {}).get("reports", []):
        for value in (report.get("threshold_for_flag_rate") or {}).values():
            if value and abs(a - value) / value < 0.01:
                return True
    return False


def validate_decision(decision: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
    action = decision.get("action")
    reason = str(decision.get("reason") or "").strip()
    if action not in ("keep", "update"):
        raise ValueError("action must be keep or update")
    if not reason:
        raise ValueError("a reason is required")
    if action == "keep":
        return {"action": "keep", "reason": reason}
    try:
        a, b = float(decision["threshold_a"]), float(decision["threshold_b"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("update needs numeric threshold_a and threshold_b") from None
    if not (ABSOLUTE_MIN <= a <= ABSOLUTE_MAX) or not (a * MIN_B_OVER_A <= b <= ABSOLUTE_MAX * 10):
        raise ValueError(f"A must be in [{ABSOLUTE_MIN}, {ABSOLUTE_MAX}] and B at least {MIN_B_OVER_A}x A")
    dist = observations.get("score_distribution") or {}
    enough_live = int(dist.get("reports_scored", 0)) >= MIN_REPORTS_TO_RETUNE
    if not enough_live and not _from_offline_report(a, observations):
        raise ValueError(
            f"fewer than {MIN_REPORTS_TO_RETUNE} scored reports on this model version, and A is not a value "
            "from an offline score_checkpoint report; keep"
        )
    current_info = observations.get("current_thresholds") or {}
    active = current_info.get("active")
    if active:
        current = float(active["threshold_a"])
        # A new model rescales every score, so the step bound only applies to the same model.
        if not current_info.get("model_changed_since_set") and not (current / MAX_STEP_FACTOR <= a <= current * MAX_STEP_FACTOR):
            raise ValueError(f"A may move at most {MAX_STEP_FACTOR}x from {current} on the same model")
        if not current_info.get("model_changed_since_set") and (
            abs(a - current) / current < HYSTERESIS
            and abs(b - float(active["threshold_b"])) / float(active["threshold_b"]) < HYSTERESIS
        ):
            return {"action": "keep", "reason": f"change under {HYSTERESIS:.0%}; kept. Original reason: {reason}"}
    return {"action": "update", "threshold_a": a, "threshold_b": b, "reason": reason}


def budget_policy(budgets: Budgets):
    """The deterministic baseline: take the scores at the budgeted flag shares."""
    def fallback(task: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
        dist = observations.get("score_distribution")
        if not dist:
            return {"action": "keep", "reason": "baseline: score_distribution was not gathered"}
        if int(dist.get("reports_scored", 0)) < MIN_REPORTS_TO_RETUNE:
            return {"action": "keep", "reason": f"baseline: only {dist.get('reports_scored', 0)} scored reports on this model version"}
        scores = dist["score_at_flag_share"]
        key_a = f"{budgets.a:g}"
        key_b = f"{budgets.b:g}"
        a = scores.get(key_a)
        b = scores.get(key_b)
        if a is None or b is None:
            return {"action": "keep", "reason": "baseline: budget shares are not among the tabulated shares"}
        a = min(max(a, ABSOLUTE_MIN), ABSOLUTE_MAX)
        b = max(b, a * MIN_B_OVER_A)
        active = (observations.get("current_thresholds") or {}).get("active")
        info = observations.get("current_thresholds") or {}
        if active and not info.get("model_changed_since_set"):
            current = float(active["threshold_a"])
            a = min(max(a, current / MAX_STEP_FACTOR), current * MAX_STEP_FACTOR)
            b = max(b, a * MIN_B_OVER_A)
        return {"action": "update", "threshold_a": a, "threshold_b": b,
                "reason": f"baseline: scores at flag shares {key_a} and {key_b} over {dist['reports_scored']} reports"}
    return fallback


def build_threshold_agent(db: Any, client: Any, model: str | None, model_version: str | None,
                          budgets: Budgets = Budgets(), reports_dir: Path | None = None) -> Agent:
    return Agent(
        "threshold_agent", SYSTEM_PROMPT, build_tools(db, model_version, reports_dir), DECISION_SCHEMA,
        validate_decision, budget_policy(budgets), client=client, model=model,
        fallback_tools=("current_thresholds", "score_distribution", "offline_reports"),
    )


async def apply_decision(db: Any, decision: dict[str, Any], model_version: str | None, set_by: str = "agent") -> bool:
    if decision.get("action") != "update":
        return False
    await db.fetchval(INSERT_QUERY, decision["threshold_a"], decision["threshold_b"], model_version, set_by, decision["reason"][:500])
    return True


async def retune(db: Any, agent: Agent, model_version: str | None, budgets: Budgets = Budgets(), dry_run: bool = False) -> dict[str, Any]:
    """Run the agent once and apply its decision. Safe to call repeatedly."""
    task = {"model_version": model_version, "alert_budget_share": {"A": budgets.a, "B": budgets.b}}
    run = await agent.run(task)
    applied = False if dry_run else await apply_decision(db, run.decision, model_version)
    await record_run(db, run)
    logger.info("threshold agent (%s): %s%s", run.mode, run.decision.get("action"), " applied" if applied else "")
    return {"mode": run.mode, "decision": run.decision, "applied": applied, "reason": run.reason}
