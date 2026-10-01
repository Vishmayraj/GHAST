"""LiveScorer tests: fake store, fake model scorer, fake investigate().

No database, torch, checkpoint, or LLM key is needed (same rule as every other suite in
this repo). The model is replaced by a callable returning a chosen per-report error array.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from live_scorer import (
    VOTE_FREEZE_REPLAY, VOTE_PREDICTION_ERROR, ActiveThresholds, DetectorThresholds, LiveScorer, ScorerConfig,
)
from models.bilstm.threshold import OPERATING_THRESHOLD
from orchestrator.state_machine import InvestigationResult, InvestigationState

pytestmark = pytest.mark.skipif(
    OPERATING_THRESHOLD is None, reason="OPERATING_THRESHOLD is None; scorer refuses to run"
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
WINDOW = 20


def make_rows(mmsi: int, frozen_tail: int = 0) -> list[dict]:
    """20 live reports, one per minute, ~10 knots due north, oldest first.

    `frozen_tail` repeats the last position that many extra times while still claiming
    10 knots: the signature freeze_replay_detector exists to catch.
    """
    rows = []
    for i in range(WINDOW):
        moving_index = min(i, WINDOW - 1 - frozen_tail)
        rows.append({
            "received_at": NOW - timedelta(minutes=WINDOW - 1 - i),
            "mmsi": mmsi,
            "latitude": 51.0 + moving_index * 0.0028,
            "longitude": 4.0,
            "sog_knots": 10.0,
            "cog_deg": 0.0,
            "true_heading_deg": 0,
            "rate_of_turn": 0,
            "navigational_status": 0,
            "ship_type": 70,
        })
    return rows


class FakeStore:
    def __init__(self, reports: dict[int, list[dict]], open_incidents: set[int] | None = None) -> None:
        self.reports = reports
        self.open_incidents = open_incidents or set()
        self.now = NOW
        self.stats: list[tuple] = []

    async def record_stats(self, model_version, reports_scored, skipped, flagged_a, flagged_b, histogram) -> None:
        self.stats.append((model_version, reports_scored, skipped, flagged_a, flagged_b, histogram))

    async def db_now(self) -> datetime:
        return self.now

    async def active_mmsis(self, since: datetime) -> list[int]:
        return [m for m, rows in self.reports.items() if rows[-1]["received_at"] > since]

    async def recent_reports(self, mmsi: int, not_before: datetime, limit: int) -> list[dict]:
        return [r for r in self.reports[mmsi] if r["received_at"] > not_before][-limit:]

    async def has_open_incident(self, mmsi: int, since: datetime) -> bool:
        return mmsi in self.open_incidents


class Recorder:
    """Stands in for agent.orchestrator.state_machine.investigate."""

    def __init__(self) -> None:
        self.anomalies = []

    async def __call__(self, anomaly, tools, persist):
        self.anomalies.append(anomaly)
        return InvestigationResult(InvestigationState.DONE, "targeted_spoof", 0.5, {}, [])


def errors_with(index: int | None, value: float) -> np.ndarray:
    errors = np.zeros(WINDOW)
    if index is not None:
        errors[index] = value
    return errors


def build(store, errors, recorder, **config_overrides) -> LiveScorer:
    config = ScorerConfig(
        thresholds=DetectorThresholds(prediction_error=float(OPERATING_THRESHOLD)),
        **config_overrides,
    )
    return LiveScorer(
        store=store,
        score_errors=lambda window: errors,
        tools={},
        persist=None,  # never reached: investigate is replaced
        config=config,
        investigate_fn=recorder,
    )


@pytest.mark.asyncio
async def test_high_prediction_error_triggers_investigation() -> None:
    recorder = Recorder()
    scorer = build(FakeStore({111: make_rows(111)}), errors_with(15, OPERATING_THRESHOLD * 50), recorder)

    summary = await scorer.poll_once()

    assert summary.windows_scored == 1 and summary.investigated == 1
    anomaly = recorder.anomalies[0]
    assert anomaly.mmsi == 111
    assert anomaly.anomaly_score == pytest.approx(OPERATING_THRESHOLD * 50)
    assert anomaly.detector_votes == frozenset({VOTE_PREDICTION_ERROR})


@pytest.mark.asyncio
async def test_flag_carries_the_scored_window_bounds() -> None:
    recorder = Recorder()
    rows = make_rows(111)
    scorer = build(FakeStore({111: rows}), errors_with(15, OPERATING_THRESHOLD * 50), recorder)

    await scorer.poll_once()

    anomaly = recorder.anomalies[0]
    assert anomaly.window_start == rows[0]["received_at"]
    assert anomaly.window_end == rows[-1]["received_at"]


@pytest.mark.asyncio
async def test_low_prediction_error_does_not_trigger() -> None:
    recorder = Recorder()
    scorer = build(FakeStore({111: make_rows(111)}), errors_with(15, OPERATING_THRESHOLD / 10), recorder)

    summary = await scorer.poll_once()

    assert summary.windows_scored == 1 and summary.flagged == 0
    assert recorder.anomalies == []


@pytest.mark.asyncio
async def test_frozen_position_votes_freeze_replay_even_with_zero_error() -> None:
    recorder = Recorder()
    scorer = build(FakeStore({222: make_rows(222, frozen_tail=3)}), errors_with(None, 0.0), recorder)

    await scorer.poll_once()

    assert len(recorder.anomalies) == 1
    assert recorder.anomalies[0].detector_votes == frozenset({VOTE_FREEZE_REPLAY})


@pytest.mark.asyncio
async def test_min_votes_two_needs_detectors_to_agree() -> None:
    # Prediction error alone (moving vessel, so no freeze vote): filtered out at 2.
    recorder = Recorder()
    scorer = build(FakeStore({111: make_rows(111)}), errors_with(15, 1.0), recorder, min_votes=2)
    assert (await scorer.poll_once()).flagged == 0

    # Both detectors on the same frozen report: passes.
    recorder = Recorder()
    errors = errors_with(19, 1.0)
    scorer = build(FakeStore({222: make_rows(222, frozen_tail=3)}), errors, recorder, min_votes=2)
    await scorer.poll_once()
    assert recorder.anomalies[0].detector_votes == frozenset({VOTE_PREDICTION_ERROR, VOTE_FREEZE_REPLAY})


@pytest.mark.asyncio
async def test_open_incident_debounces_investigation() -> None:
    recorder = Recorder()
    store = FakeStore({111: make_rows(111)}, open_incidents={111})
    scorer = build(store, errors_with(15, 1.0), recorder)

    summary = await scorer.poll_once()

    assert summary.flagged == 1 and summary.debounced == 1 and summary.investigated == 0
    assert recorder.anomalies == []


@pytest.mark.asyncio
async def test_same_vessel_is_not_reinvestigated_on_next_poll() -> None:
    recorder = Recorder()
    store = FakeStore({111: make_rows(111)})
    scorer = build(store, errors_with(15, 1.0), recorder)

    await scorer.poll_once()
    store.now = NOW + timedelta(seconds=30)  # no new reports arrive
    second = await scorer.poll_once()

    assert len(recorder.anomalies) == 1
    assert second.investigated == 0


@pytest.mark.asyncio
async def test_per_cycle_cap_defers_then_retries_next_cycle() -> None:
    recorder = Recorder()
    store = FakeStore({1: make_rows(1), 2: make_rows(2)})
    scorer = build(store, errors_with(15, 1.0), recorder, max_investigations_per_cycle=1)

    first = await scorer.poll_once()
    assert first.investigated == 1 and first.deferred == 1

    store.now = NOW + timedelta(seconds=30)
    second = await scorer.poll_once()
    assert second.investigated == 1
    assert {a.mmsi for a in recorder.anomalies} == {1, 2}


@pytest.mark.asyncio
async def test_short_history_vessel_is_skipped() -> None:
    recorder = Recorder()
    scorer = build(FakeStore({9: make_rows(9)[-5:]}), errors_with(15, 1.0), recorder)

    summary = await scorer.poll_once()

    assert summary.short_history == 1 and summary.windows_scored == 0
    assert recorder.anomalies == []


@pytest.mark.asyncio
async def test_build_tools_registers_pattern_classifier_as_neutral_stub_by_default() -> None:
    from live_scorer import build_tools

    tools = build_tools(db=None)  # the stub never touches the database

    assert set(tools) == {"track_history", "jamming_zones", "incident_history", "pattern_classifier"}
    from orchestrator.state_machine import FlaggedAnomaly
    result = await tools["pattern_classifier"](FlaggedAnomaly(1, NOW, 0.1, "x", 0.0, 0.0))
    assert result["available"] is False


@pytest.mark.asyncio
async def test_long_gap_before_a_report_cannot_flag_it_and_is_counted() -> None:
    rows = make_rows(111)
    for row in rows[15:]:  # a 40 minute silence before report 15, then normal cadence
        row["received_at"] += timedelta(minutes=40)
    recorder = Recorder()
    store = FakeStore({111: rows})
    store.now = rows[-1]["received_at"] + timedelta(seconds=1)
    scorer = build(store, errors_with(15, OPERATING_THRESHOLD * 500), recorder, window_max_age_hours=24.0)

    summary = await scorer.poll_once()

    assert summary.windows_scored == 1 and summary.investigated == 0
    assert summary.skipped_unscorable == 1


@pytest.mark.asyncio
async def test_sentinel_coordinate_cannot_flag_a_report() -> None:
    rows = make_rows(111)
    rows[15]["latitude"], rows[15]["longitude"] = 91.0, 181.0
    recorder = Recorder()
    scorer = build(FakeStore({111: rows}), errors_with(15, OPERATING_THRESHOLD * 500), recorder)

    summary = await scorer.poll_once()

    assert summary.investigated == 0 and summary.skipped_unscorable >= 1


def build_two_tier(store, errors, recorder, active, **kwargs) -> LiveScorer:
    async def source():
        return active
    config = ScorerConfig(thresholds=DetectorThresholds(prediction_error=999.0))  # must be overridden by the source
    return LiveScorer(store, lambda window: errors, {}, None, config, investigate_fn=recorder,
                      threshold_source=source, model_version="v2", **kwargs)


@pytest.mark.asyncio
async def test_score_between_a_and_b_is_tier_a_and_above_b_is_tier_b() -> None:
    for score, tier in ((0.5, "A"), (2.0, "B"), (1.0, "B")):
        recorder = Recorder()
        scorer = build_two_tier(FakeStore({1: make_rows(1)}), errors_with(15, score), recorder, ActiveThresholds(0.1, 1.0, "v2"))
        summary = await scorer.poll_once()
        assert recorder.anomalies[0].tier == tier and recorder.anomalies[0].threshold_a == 0.1
        assert (summary.flagged_a, summary.flagged_b) == ((1, 0) if tier == "A" else (0, 1))


@pytest.mark.asyncio
async def test_below_threshold_a_nothing_is_flagged_but_everything_is_recorded() -> None:
    store, recorder = FakeStore({1: make_rows(1)}), Recorder()
    scorer = build_two_tier(store, errors_with(15, 0.05), recorder, ActiveThresholds(0.1, 1.0, "v2"))
    summary = await scorer.poll_once()
    assert summary.flagged == 0 and not recorder.anomalies
    model_version, scored, skipped, fa, fb, histogram = store.stats[0]
    assert model_version == "v2" and scored == 19 and skipped == 0 and (fa, fb) == (0, 0) and sum(histogram) == 19


@pytest.mark.asyncio
async def test_without_thresholds_the_scorer_observes_only_and_logs_once(caplog) -> None:
    import logging
    store, recorder = FakeStore({1: make_rows(1)}), Recorder()
    scorer = build_two_tier(store, errors_with(15, 50.0), recorder, None)
    with caplog.at_level(logging.WARNING, logger="ghast.scoring"):
        first = await scorer.poll_once()
        store.reports = {1: [{**r, "received_at": r["received_at"] + timedelta(minutes=30)} for r in make_rows(1)]}
        store.now = NOW + timedelta(minutes=31)
        await scorer.poll_once()
    assert first.observing_only and first.flagged == 0 and not recorder.anomalies
    assert store.stats and sum(r.getMessage().startswith("no alert thresholds") for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_follow_up_agents_run_after_persist_and_cannot_break_the_cycle() -> None:
    seen = []

    async def after(anomaly, result):
        seen.append(anomaly.tier)
        raise RuntimeError("follow-up broke")
    recorder = Recorder()
    scorer = build_two_tier(FakeStore({1: make_rows(1)}), errors_with(15, 2.0), recorder, ActiveThresholds(0.1, 1.0, "v2"), after_persist=after)
    summary = await scorer.poll_once()
    assert seen == ["B"] and summary.investigated == 1 and summary.failed == 0


@pytest.mark.asyncio
async def test_retune_runs_at_start_and_not_every_cycle() -> None:
    calls = []

    async def retune():
        calls.append(1)
    store = FakeStore({1: make_rows(1)})
    scorer = build_two_tier(store, errors_with(None, 0), Recorder(), ActiveThresholds(0.1, 1.0, "v2"), retune=retune)
    await scorer.poll_once()
    await scorer.poll_once()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_model_change_triggers_a_retune_even_inside_the_interval() -> None:
    calls = []

    async def retune():
        calls.append(1)
    store = FakeStore({1: make_rows(1)})
    scorer = build_two_tier(store, errors_with(None, 0), Recorder(), ActiveThresholds(0.1, 1.0, "OLD-MODEL"), retune=retune)
    await scorer.poll_once()
    store.now = NOW + timedelta(minutes=10)
    await scorer.poll_once()
    assert len(calls) == 2  # start, then again because the active thresholds belong to another model
