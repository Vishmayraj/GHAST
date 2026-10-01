import json
from types import SimpleNamespace as NS

import pytest

from features.score_histogram import histogram_counts
from threshold_agent.agent import (
    Budgets, MIN_REPORTS_TO_RETUNE, apply_decision, build_threshold_agent, retune, validate_decision,
)
from runtime.agent import AgentRun

import numpy as np


def rows_for(errors, version="v2"):
    return [{"reports_scored": len(errors), "skipped_unscorable": 5, "flagged_a": 0, "flagged_b": 0,
             "histogram": json.dumps(histogram_counts(errors))}]


class FakeDb:
    def __init__(self, active=None, stats=None):
        self.active, self.stats, self.inserted, self.runs = active, stats or [], [], []

    async def fetchrow(self, query, *a):
        return self.active

    async def fetch(self, query, *a):
        if "scoring_stats" in query:
            return self.stats
        return []

    async def fetchval(self, query, *a):
        self.inserted.append(a)
        return 1

    async def execute(self, query, *a):
        self.runs.append(a)


def active(a=0.05, b=0.5, version="v1"):
    return {"threshold_a": a, "threshold_b": b, "model_version": version, "set_by": "manual", "reason": None, "created_at": None}


ERRORS = 10 ** np.random.default_rng(3).normal(-2.3, 0.5, 80_000)


@pytest.mark.asyncio
async def test_no_llm_baseline_sets_thresholds_at_the_budgeted_flag_shares():
    db = FakeDb(active=active(a=0.15, b=1.0, version="v2"), stats=rows_for(ERRORS))
    agent = build_threshold_agent(db, None, None, "v2", Budgets(1e-3, 1e-4))
    result = await retune(db, agent, "v2", Budgets(1e-3, 1e-4))
    assert result["mode"] == "fallback" and result["decision"]["action"] == "update" and result["applied"]
    a, b, version, set_by, _ = db.inserted[0]
    assert float(np.mean(ERRORS > a)) == pytest.approx(1e-3, rel=0.5)
    assert b >= a * 1.5 and version == "v2" and set_by == "agent"
    assert db.runs and db.runs[0][0] == "threshold_agent"


@pytest.mark.asyncio
async def test_too_little_data_keeps_the_active_thresholds():
    db = FakeDb(active=active(version="v2"), stats=rows_for(ERRORS[:1000]))
    result = await retune(db, build_threshold_agent(db, None, None, "v2"), "v2", Budgets(1e-3, 1e-4))
    assert result["decision"]["action"] == "keep" and not db.inserted


@pytest.mark.asyncio
async def test_dry_run_applies_nothing():
    db = FakeDb(active=active(version="v2"), stats=rows_for(ERRORS))
    result = await retune(db, build_threshold_agent(db, None, None, "v2"), "v2", Budgets(1e-3, 1e-4), dry_run=True)
    assert result["decision"]["action"] == "update" and not result["applied"] and not db.inserted


def obs(scored=MIN_REPORTS_TO_RETUNE + 1, current=0.05, changed=False):
    return {"score_distribution": {"reports_scored": scored},
            "current_thresholds": {"active": active(a=current, b=current * 10), "model_changed_since_set": changed}}


def test_validation_enforces_every_hard_bound():
    ok = {"action": "update", "threshold_a": 0.06, "threshold_b": 0.9, "reason": "r"}
    assert validate_decision(ok, obs())["action"] == "update"
    for bad, why in [
        ({**ok, "threshold_b": 0.07}, "1.5x"),
        ({**ok, "threshold_a": 0.5, "threshold_b": 5.0}, "at most"),
        ({**ok, "threshold_a": 1e-6, "threshold_b": 1e-3}, "must be in"),
        ({**ok, "reason": " "}, "reason"),
        ({"action": "nope", "reason": "r"}, "action"),
        ({"action": "update", "reason": "r"}, "numeric"),
    ]:
        with pytest.raises(ValueError, match=why):
            validate_decision(bad, obs())
    with pytest.raises(ValueError, match="fewer than"):
        validate_decision(ok, obs(scored=10))


def test_a_new_model_may_rescale_freely_and_small_moves_are_ignored():
    jump = {"action": "update", "threshold_a": 2.0, "threshold_b": 9.0, "reason": "new model"}
    assert validate_decision(jump, obs(current=0.005, changed=True))["action"] == "update"
    tiny = {"action": "update", "threshold_a": 0.052, "threshold_b": 0.52, "reason": "r"}
    assert validate_decision(tiny, obs())["action"] == "keep"


def test_offline_report_value_can_bootstrap_a_new_model_with_no_live_data():
    o = obs(scored=0, current=0.005, changed=True)
    o["offline_reports"] = {"reports": [{"threshold_for_flag_rate": {"0.001": 0.12}}]}
    good = {"action": "update", "threshold_a": 0.12, "threshold_b": 1.0, "reason": "offline"}
    assert validate_decision(good, o)["action"] == "update"
    with pytest.raises(ValueError, match="offline"):
        validate_decision({**good, "threshold_a": 0.3}, o)


@pytest.mark.asyncio
async def test_llm_agent_uses_tools_and_its_decision_is_applied():
    def call(name, args, i):
        return NS(id=i, function=NS(name=name, arguments=json.dumps(args)))
    replies = [
        NS(choices=[NS(message=NS(content=None, tool_calls=[call("current_thresholds", {}, "1"), call("score_distribution", {}, "2")]))]),
        NS(choices=[NS(message=NS(content=None, tool_calls=[call("finish", {"action": "update", "threshold_a": 0.06, "threshold_b": 0.9, "reason": "budget"}, "3")]))]),
    ]
    client = NS(chat=NS(completions=NS(create=None)))
    async def create(**kw):
        return replies.pop(0)
    client.chat.completions.create = create
    db = FakeDb(active=active(version="v2"), stats=rows_for(ERRORS))
    result = await retune(db, build_threshold_agent(db, client, "m", "v2"), "v2")
    assert result["mode"] == "llm" and result["applied"] and db.inserted[0][0] == 0.06


@pytest.mark.asyncio
async def test_keep_writes_nothing():
    db = FakeDb()
    assert await apply_decision(db, {"action": "keep", "reason": "r"}, "v") is False and not db.inserted
