import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from agents.challenger import baseline as challenger_baseline, build_challenger_agent, validate as challenger_validate
from agents.context import IncidentContext
from agents.fleet_context import (
    NEARBY_INCIDENTS_QUERY, NEIGHBOURS_QUERY, baseline as fleet_baseline, build_fleet_context_agent, validate as fleet_validate,
)
from agents.triage import MAX_CHALLENGER_EFFECT, baseline as triage_baseline, build_triage_agent, validate as triage_validate

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def ctx(**kw) -> IncidentContext:
    base = dict(incident_id="i1", mmsi=111, flagged_at=T0, latitude=51.0, longitude=4.0, score=0.5, tier="A",
                hypothesis="targeted_spoof", confidence=0.72, votes=("prediction_error",),
                evidence={"track_history": {"positions": [{"sog_knots": 10.0}] * 20}}, threshold_a=0.2, threshold_b=1.0)
    return IncidentContext(**{**base, **kw})


class Db:
    def __init__(self, neighbours=0, incidents=0, profile=None, history=(), open_a=0):
        self.neighbours, self.incidents, self.profile, self.history, self.open_a = neighbours, incidents, profile, list(history), open_a
        self.queries = []

    async def fetch(self, query, *args):
        self.queries.append(query)
        if query == NEIGHBOURS_QUERY:
            return [{"km": float(i + 1)} for i in range(self.neighbours)]
        if query == NEARBY_INCIDENTS_QUERY:
            return [{"mmsi": 1000 + i, "hypothesis": "jamming", "lat": 51.0 + 0.01 * (i + 1), "lon": 4.0, "flagged_at": T0} for i in range(self.incidents)]
        return self.history

    async def fetchrow(self, query, *args):
        if "count(*) AS open_a" in query:
            return {"open_a": self.open_a}
        return self.profile


# --- fleet context -------------------------------------------------------------------------
def fleet_obs(neighbours, incidents, cluster=None):
    return {"vessels_nearby": {"count": neighbours}, "incidents_nearby": {"count": incidents, "cluster_vessels": incidents + 1 if cluster is None else cluster}}


@pytest.mark.parametrize("neighbours,incidents,scope", [(10, 0, "isolated"), (10, 3, "area"), (1, 0, "insufficient"), (0, 2, "area")])
def test_fleet_baseline_scope(neighbours, incidents, scope):
    assert fleet_baseline({}, fleet_obs(neighbours, incidents))["scope"] == scope


def test_fleet_validate_refuses_isolated_without_neighbours_and_area_without_incidents():
    with pytest.raises(ValueError, match="isolated"):
        fleet_validate({"scope": "isolated", "note": "n"}, fleet_obs(1, 0))
    with pytest.raises(ValueError, match="area needs"):
        fleet_validate({"scope": "area", "note": "n"}, fleet_obs(9, 1))
    with pytest.raises(ValueError, match="call vessels_nearby"):
        fleet_validate({"scope": "insufficient", "note": "n"}, {})


def test_fleet_counts_come_from_the_tools_not_the_model():
    out = fleet_validate({"scope": "isolated", "neighbours_checked": 999, "nearby_incidents": 999, "note": "n"}, fleet_obs(8, 0))
    assert (out["neighbours_checked"], out["nearby_incidents"]) == (8, 0)


@pytest.mark.asyncio
async def test_fleet_agent_without_llm_queries_both_tools_and_decides():
    db = Db(neighbours=12, incidents=0)
    run = await build_fleet_context_agent(db, ctx(), None, None).run({})
    assert run.mode == "fallback" and run.decision["scope"] == "isolated" and {NEIGHBOURS_QUERY, NEARBY_INCIDENTS_QUERY} <= set(db.queries)


class IncidentRows(Db):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    async def fetch(self, query, *args):
        return self.rows if query == NEARBY_INCIDENTS_QUERY else []


def incident_row(mmsi, lat, lon=4.0, hours=0.0):
    from datetime import timedelta
    return {"mmsi": mmsi, "hypothesis": "benign", "lat": lat, "lon": lon, "flagged_at": T0 + timedelta(hours=hours)}


async def nearby(rows, radius=25):
    agent = build_fleet_context_agent(IncidentRows(rows), ctx(), None, None)
    return await agent._tools["incidents_nearby"].fn({"radius_km": radius})


@pytest.mark.asyncio
async def test_incidents_close_together_form_a_cluster_with_this_vessel():
    result = await nearby([incident_row(2, 51.02), incident_row(3, 51.04)])
    assert result["count"] == 2 and result["cluster_vessels"] == 3


@pytest.mark.asyncio
async def test_incidents_inside_the_radius_but_far_from_each_other_are_not_an_area():
    # both within 100 km of the flag, but each is 55 km from it and 111 km from the other
    result = await nearby([incident_row(2, 51.5), incident_row(3, 50.5)], radius=100)
    assert result["count"] == 2 and result["cluster_vessels"] == 1


@pytest.mark.asyncio
async def test_incidents_from_other_hours_do_not_join_the_cluster():
    result = await nearby([incident_row(2, 51.02, hours=3), incident_row(3, 51.04, hours=-4)])
    assert result["count"] == 2 and result["cluster_vessels"] == 1


@pytest.mark.asyncio
async def test_fleet_agent_calls_a_real_cluster_an_area_and_scattered_incidents_isolated():
    clustered = IncidentRows([incident_row(2, 51.02), incident_row(3, 51.04)])
    clustered.neighbours = 0
    run = await build_fleet_context_agent(clustered, ctx(), None, None).run({})
    assert run.decision["scope"] == "area" and run.decision["cluster_vessels"] == 3


def test_fleet_validate_refuses_area_when_incidents_are_not_a_cluster():
    with pytest.raises(ValueError, match="cluster of at least 3"):
        fleet_validate({"scope": "area", "note": "n"}, fleet_obs(9, 5, cluster=1))
    assert fleet_validate({"scope": "area", "note": "n"}, fleet_obs(9, 2, cluster=3))["cluster_vessels"] == 3


@pytest.mark.asyncio
async def test_fleet_radius_is_capped():
    seen = []

    class D(Db):
        async def fetch(self, query, *args):
            seen.append(args[3]); return []
    agent = build_fleet_context_agent(D(), ctx(), None, None)
    await agent._tools["vessels_nearby"].fn({"radius_km": 5000})
    assert seen == [100.0]


# --- challenger ----------------------------------------------------------------------------
def test_challenger_baseline_rewards_independent_benign_signs_and_never_reaches_certainty():
    obs = {"track_motion": {"stationary": True}, "score_context": {"score_over_threshold_a": 1.2, "corroborated": False},
           "own_history": {"benign_verdicts": 2}}
    out = challenger_baseline({}, obs)
    assert 0.9 >= out["benign_likelihood"] > 0.8 and "stationary" in out["argument"]
    quiet = challenger_baseline({}, {"track_motion": {"stationary": False}, "score_context": {"score_over_threshold_a": 9.0, "corroborated": True},
                                     "own_history": {"benign_verdicts": 0}})
    assert quiet["benign_likelihood"] == 0.0 and "no benign explanation" in quiet["argument"]


def test_challenger_validate_demands_range_argument_and_two_tools():
    ok = {"benign_likelihood": 0.4, "argument": "a", "cited": ["track_motion", "ghost"]}
    obs = {"track_motion": {}, "own_history": {}}
    assert challenger_validate(ok, obs)["cited"] == ["track_motion"]
    for bad, why in [({**ok, "benign_likelihood": 1.5}, "between"), ({**ok, "argument": ""}, "argument"), ({**ok, "benign_likelihood": "x"}, "number")]:
        with pytest.raises(ValueError, match=why):
            challenger_validate(bad, obs)
    with pytest.raises(ValueError, match="at least 2"):
        challenger_validate(ok, {"track_motion": {}})


@pytest.mark.asyncio
async def test_challenger_agent_uses_stored_evidence_and_history():
    db = Db(history=[{"hypothesis": "benign", "tier": "A", "review_verdict": "benign", "flagged_at": T0}])
    run = await build_challenger_agent(db, ctx(), None, None).run({})
    assert run.mode == "fallback" and run.observations["own_history"]["benign_verdicts"] == 1
    assert run.observations["score_context"]["score_over_threshold_a"] == pytest.approx(2.5)
    assert run.decision["benign_likelihood"] > 0


# --- triage --------------------------------------------------------------------------------
CARD = {"confidence": 0.8, "score": 0.6, "threshold_a": 0.2, "threshold_b": 1.0}


def test_triage_priority_rises_with_confidence_strength_and_area_scope():
    low = triage_baseline({"card": {**CARD, "confidence": 0.3, "score": 0.2}, "fleet_context": {"scope": "isolated"}}, {})
    high = triage_baseline({"card": {**CARD, "confidence": 0.9, "score": 1.0}, "fleet_context": {"scope": "area"}}, {})
    assert 0 <= low["priority"] < high["priority"] <= 1


def test_the_challenger_can_lower_priority_by_at_most_a_quarter():
    task = {"card": CARD, "fleet_context": {"scope": "isolated"}}
    without = triage_baseline({**task, "challenge": {"benign_likelihood": 0.0}}, {})["priority"]
    full = triage_baseline({**task, "challenge": {"benign_likelihood": 1.0}}, {})["priority"]
    assert full >= without * (1 - MAX_CHALLENGER_EFFECT) - 0.002 and full < without


def test_triage_validate_range_rationale_and_card_read():
    obs = {"incident_card": {}}
    assert triage_validate({"priority": 0.5, "rationale": "r"}, obs)["priority"] == 0.5
    for bad, why in [({"priority": 2, "rationale": "r"}, "between"), ({"priority": 0.5, "rationale": ""}, "rationale"), ({"priority": "x", "rationale": "r"}, "number")]:
        with pytest.raises(ValueError, match=why):
            triage_validate(bad, obs)
    with pytest.raises(ValueError, match="incident_card"):
        triage_validate({"priority": 0.5, "rationale": "r"}, {})


@pytest.mark.asyncio
async def test_triage_agent_reads_queue_pressure_and_decides_without_llm():
    run = await build_triage_agent(Db(open_a=7), ctx(), None, None).run({"card": ctx().card(), "fleet_context": None, "challenge": None})
    assert run.mode == "fallback" and run.observations["queue_pressure"] == {"unreviewed_tier_a_last_24h": 7}
    assert 0 <= run.decision["priority"] <= 1


def test_the_card_hands_agents_laya_and_corroboration_without_the_raw_track():
    card = ctx(evidence={"pattern_classifier": {"available": True, "pattern": "freeze_replay", "confidence": 0.9},
                         "freeze_corroboration": {"matched": True}, "track_history": {"positions": [1] * 500}}).card()
    assert card["laya"]["pattern"] == "freeze_replay" and card["freeze_corroborated"] and "track_history" not in json.dumps(card)
