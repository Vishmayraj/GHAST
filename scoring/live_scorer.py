"""Live scoring service: poll new AIS reports, score them, hand real flags to the agent.

Glue only, no new modeling (ImplementationPlans/old/Sem5_BigPass_LiveScoring_And_Laya.md
section 4). For each vessel with new live reports it:

  1. pulls the vessel's most recent WINDOW_LENGTH reports and builds a FeatureWindow with
     the same code training uses (features.pipeline.window_rows -> features.extract),
  2. scores it with ml/models/bilstm/infer.py::prediction_errors (single-window path),
  3. runs the same detectors evaluation ran (prediction_error over OPERATING_THRESHOLD,
     freeze_replay_detector, and optionally speed_jump_detector) on each *new* report,
  4. when enough detectors agree, builds a FlaggedAnomaly recording which detectors voted
     and calls agent.orchestrator.state_machine.investigate() with the existing tools.

Design notes worth knowing before changing anything:

* Only reports newer than what this process already scored are considered, otherwise the
  same old spike would re-flag on every poll while it stays inside the 20-report window.
* A vessel with an unresolved incident newer than --debounce-hours is skipped, and so is a
  vessel this process already investigated inside that window (covers a failed persist).
* --max-investigations-per-cycle defaults to 25. Candidates beyond that cap are retained
  in an in-memory deferred queue and retried on the next completed poll; they are not
  silently acknowledged. A process restart loses only that transient queue, while the
  normal watermark/overlap scan will discover subsequent reports again.
* The followup evaluation measured a 19.4% control false-positive rate for the
  prediction-error detector, so in live traffic a large share of single-detector flags
  are expected to be noise. The state machine caps single-detector confidence so those
  escalate instead of being reported, and --max-investigations-per-cycle bounds how many
  investigations one poll can start. Both are first-pass mitigations; per-vessel-class or per-region threshold normalization is future work.
* Alerting is two-tier. Thresholds A and B are read from `threshold_config` every cycle and
  kept right by the threshold agent (agent/threshold_agent), which looks again on a schedule
  and whenever the model version changes. A score at or above A becomes a console incident;
  at or above B (B > A) it is tier B: its report is drafted automatically and it is listed
  first. With no row in `threshold_config` the scorer only observes: it scores everything and
  records the score histogram the threshold agent learns from, and flags nothing.
  `--static-threshold` restores the old single constant.
* Transitions the model cannot fairly score (sentinel coordinates, long silences, bad time
  steps, antimeridian crossings; ml/features/quality.py) never vote and are counted per cycle.
* freeze_replay's trigger defaults to features.extract.FREEZE_DISPLACEMENT_EPSILON_KNOTS,
  matching the agent's own corroboration check. speed_jump has no swept threshold on
  record yet, so it does not vote unless --speed-jump-threshold is given.

Usage (from the repo root, with the ml/ and agent/ dependencies installed):
    cd scoring
    python live_scorer.py --dsn postgresql://ghast:ghast@localhost:5432/ghast \\
        --checkpoint ../ml/checkpoints/epoch_010.pt --poll-interval-seconds 30
    # one cycle over the last 6 hours of live data, then exit:
    python live_scorer.py ... --once --initial-lookback-minutes 360

Scoring itself calls no LLM. After an incident is stored, `agents.pipeline` runs the fleet-context,
challenger and triage agents on it, and drafts the report for tier B only
(agent/report_generator/on_demand.py); tier A reports are drafted on a button press. Without
GROQ_API_KEY every agent falls back to its deterministic baseline and scoring is unaffected.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np

# The repo's packages use flat imports (see agent/pytest.ini, ml/pytest.ini); make
# `python live_scorer.py` work from a checkout without PYTHONPATH gymnastics.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _package_dir in ("ml", "agent"):
    _path = str(_REPO_ROOT / _package_dir)
    if _path not in sys.path:
        sys.path.append(_path)

from evaluation.baselines import freeze_replay_detector, speed_jump_detector  # noqa: E402
from evaluation.datasets import AISObservation  # noqa: E402
from features.extract import (  # noqa: E402
    COG_INDEX, FREEZE_DISPLACEMENT_EPSILON_KNOTS, HEADING_INDEX, IMPLIED_SPEED_INDEX,
    MISSING_VALUE, N_FEATURES, SOG_INDEX,
)
from features.pipeline import WINDOW_LENGTH, FeatureWindow, window_rows  # noqa: E402
from features.quality import transition_reasons  # noqa: E402
from features.score_histogram import histogram_counts  # noqa: E402
from models.bilstm.threshold import OPERATING_THRESHOLD  # noqa: E402
from orchestrator.state_machine import (  # noqa: E402
    FlaggedAnomaly, InvestigationResult, Persist, Tool, investigate, persist_incident,
)
from tools.incident_history import find_similar_incidents  # noqa: E402
from tools.jamming_zones import check_jamming_zones  # noqa: E402
from tools.pattern_classifier import Predict, build_pattern_classifier, load_laya_predictor  # noqa: E402
from tools.track_history import get_track_history  # noqa: E402
from agents.pipeline import IncidentPipeline  # noqa: E402
from report_generator.on_demand import purge_expired_reports  # noqa: E402
from threshold_agent.agent import Budgets, DEFAULT_BUDGET_A, DEFAULT_BUDGET_B, build_threshold_agent, retune as retune_thresholds  # noqa: E402

logger = logging.getLogger("ghast.scoring")

VOTE_PREDICTION_ERROR = "prediction_error"
VOTE_FREEZE_REPLAY = "freeze_replay"
VOTE_SPEED_JUMP = "speed_jump"

DEFAULT_POLL_INTERVAL_SECONDS = 30.0
DEFAULT_DEBOUNCE_HOURS = 6.0
DEFAULT_MIN_VOTES = 1
DEFAULT_MAX_INVESTIGATIONS_PER_CYCLE = 25
DEFAULT_INITIAL_LOOKBACK_MINUTES = 30.0
# Ingestion flushes position batches on a timer (ingestion/main.py), so a row's
# received_at can commit a few seconds after newer rows do; re-reading a small overlap
# each poll avoids missing those. Rescoring is harmless: last_scored skips old reports.
DEFAULT_OVERLAP_SECONDS = 30.0
# A window whose oldest report is many hours old is not a coherent trajectory.
DEFAULT_WINDOW_MAX_AGE_HOURS = 6.0

ACTIVE_VESSELS_QUERY = """
SELECT DISTINCT mmsi FROM vessel_position
WHERE received_at > $1 AND message_type IS DISTINCT FROM 'historical'
"""

# Same columns and live/historical filter as features.pipeline.POSITION_QUERY, so the
# rows feed features.extract.extract_features exactly as they do in training.
RECENT_REPORTS_QUERY = """
SELECT vp.received_at, vp.mmsi, vp.latitude, vp.longitude, vp.sog_knots,
       vp.cog_deg, vp.true_heading_deg, vp.rate_of_turn, vp.navigational_status,
       vs.ship_type
FROM vessel_position AS vp
LEFT JOIN vessel_static AS vs ON vs.mmsi = vp.mmsi
WHERE vp.mmsi = $1 AND vp.received_at > $2
  AND vp.message_type IS DISTINCT FROM 'historical'
ORDER BY vp.received_at DESC
LIMIT $3
"""

# incidents.status is reported | escalated | resolved (backend/models/schema.sql);
# anything not resolved is still open.
OPEN_INCIDENT_QUERY = """
SELECT 1 FROM incidents WHERE mmsi = $1 AND status <> 'resolved' AND created_at > $2 LIMIT 1
"""


ACTIVE_THRESHOLDS_QUERY = """
SELECT threshold_a, threshold_b, model_version FROM threshold_config ORDER BY id DESC LIMIT 1
"""

RECORD_STATS_QUERY = """
INSERT INTO scoring_stats (model_version, reports_scored, skipped_unscorable, flagged_a, flagged_b, histogram)
VALUES ($1, $2, $3, $4, $5, $6::jsonb)
"""


@dataclass(frozen=True)
class ActiveThresholds:
    """Alert thresholds in force (degrees of prediction error). B > A."""

    a: float
    b: float
    model_version: str | None = None


ThresholdSource = Callable[[], Awaitable["ActiveThresholds | None"]]
AfterPersist = Callable[["FlaggedAnomaly", "InvestigationResult"], Awaitable[None]]
Retune = Callable[[], Awaitable[Any]]

# The threshold agent is asked to look again at least this often, and immediately whenever the
# model version differs from the one the active thresholds were set for.
DEFAULT_RETUNE_INTERVAL_SECONDS = 3600.0


@dataclass(frozen=True)
class DetectorThresholds:
    """Per-detector triggers, using evaluation's strict `score > threshold` comparison."""

    prediction_error: float
    freeze_replay: float = FREEZE_DISPLACEMENT_EPSILON_KNOTS
    speed_jump: float | None = None  # None: no swept threshold on record, detector abstains


@dataclass(frozen=True)
class ScorerConfig:
    thresholds: DetectorThresholds
    min_votes: int = DEFAULT_MIN_VOTES
    debounce_hours: float = DEFAULT_DEBOUNCE_HOURS
    max_investigations_per_cycle: int = DEFAULT_MAX_INVESTIGATIONS_PER_CYCLE
    initial_lookback_minutes: float = DEFAULT_INITIAL_LOOKBACK_MINUTES
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS
    window_max_age_hours: float = DEFAULT_WINDOW_MAX_AGE_HOURS
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS


@dataclass
class CycleSummary:
    vessels_seen: int = 0
    short_history: int = 0
    windows_scored: int = 0
    flagged: int = 0
    debounced: int = 0
    deferred: int = 0
    investigated: int = 0
    failed: int = 0
    skipped_unscorable: int = 0
    flagged_a: int = 0
    flagged_b: int = 0
    observing_only: bool = False

    def __str__(self) -> str:
        return " ".join(f"{name}={value}" for name, value in vars(self).items())


class Store(Protocol):
    """The scorer's whole view of the database, so tests can swap in a fake."""

    async def db_now(self) -> datetime: ...
    async def active_mmsis(self, since: datetime) -> list[int]: ...
    async def recent_reports(self, mmsi: int, not_before: datetime, limit: int) -> list[dict[str, Any]]: ...
    async def has_open_incident(self, mmsi: int, since: datetime) -> bool: ...
    async def record_stats(
        self, model_version: str | None, reports_scored: int, skipped: int, flagged_a: int, flagged_b: int,
        histogram: list[int],
    ) -> None: ...


class PostgresStore:
    """`db` is an asyncpg Pool or Connection; both expose fetch/fetchval the same way."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def db_now(self) -> datetime:
        # The database's clock, not this host's, so watermarks match received_at.
        return await self._db.fetchval("SELECT now()")

    async def active_mmsis(self, since: datetime) -> list[int]:
        rows = await self._db.fetch(ACTIVE_VESSELS_QUERY, since)
        return [int(row["mmsi"]) for row in rows]

    async def recent_reports(self, mmsi: int, not_before: datetime, limit: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch(RECENT_REPORTS_QUERY, mmsi, not_before, limit)
        return [dict(row) for row in reversed(rows)]  # oldest first, as window_rows expects

    async def has_open_incident(self, mmsi: int, since: datetime) -> bool:
        return await self._db.fetchval(OPEN_INCIDENT_QUERY, mmsi, since) is not None

    async def record_stats(
        self, model_version: str | None, reports_scored: int, skipped: int, flagged_a: int, flagged_b: int,
        histogram: list[int],
    ) -> None:
        await self._db.execute(RECORD_STATS_QUERY, model_version, reports_scored, skipped, flagged_a, flagged_b, json.dumps(histogram))


class PostgresThresholds:
    """Reads the newest threshold_config row (written by the threshold agent or an analyst)."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def __call__(self) -> ActiveThresholds | None:
        row = await self._db.fetchrow(ACTIVE_THRESHOLDS_QUERY)
        if row is None:
            return None
        return ActiveThresholds(float(row["threshold_a"]), float(row["threshold_b"]), row["model_version"])


def _implied_acceleration(features: np.ndarray) -> list[float | None]:
    """Per-report change in reported SOG, same derivation as
    evaluation/score_checkpoint.py::_implied_acceleration (kept as a local copy because
    that module imports torch and the evaluation stack at import time)."""
    accelerations: list[float | None] = [None]
    for index in range(1, len(features)):
        previous_sog, current_sog = features[index - 1, SOG_INDEX], features[index, SOG_INDEX]
        if previous_sog == MISSING_VALUE or current_sog == MISSING_VALUE:
            accelerations.append(None)
        else:
            accelerations.append(float(current_sog - previous_sog))
    return accelerations


def window_observations(window: FeatureWindow, errors: np.ndarray) -> list[AISObservation]:
    """Flatten a scored window into the AISObservation shape the detectors consume.

    Real reports carry no label: `is_spoofed` is a placeholder that no detector reads.
    """
    accelerations = _implied_acceleration(window.features)
    observations: list[AISObservation] = []
    for index in range(len(window.positions)):
        row_features = window.features[index]
        latitude, longitude = window.positions[index]
        observations.append(AISObservation(
            mmsi=str(window.mmsi),
            timestamp=window.timestamps[index].isoformat(),
            latitude=float(latitude),
            longitude=float(longitude),
            sog=float(row_features[SOG_INDEX]),
            cog=float(row_features[COG_INDEX]),
            heading=float(row_features[HEADING_INDEX]),
            is_spoofed=False,
            prediction_error=float(errors[index]),
            acceleration=accelerations[index],
            implied_speed=float(row_features[IMPLIED_SPEED_INDEX]),
            source="live",
        ))
    return observations


def detector_votes(observation: AISObservation, thresholds: DetectorThresholds) -> frozenset[str]:
    """Which detectors independently flag this one report."""
    votes: set[str] = set()
    if observation.prediction_error is not None and observation.prediction_error > thresholds.prediction_error:
        votes.add(VOTE_PREDICTION_ERROR)
    if freeze_replay_detector(observation) > thresholds.freeze_replay:
        votes.add(VOTE_FREEZE_REPLAY)
    if thresholds.speed_jump is not None and speed_jump_detector(observation) > thresholds.speed_jump:
        votes.add(VOTE_SPEED_JUMP)
    return frozenset(votes)


def evaluate_window(
    window: FeatureWindow, errors: np.ndarray, cutoff: datetime, thresholds: DetectorThresholds, min_votes: int,
    threshold_b: float | None = None,
) -> FlaggedAnomaly | None:
    """Return a FlaggedAnomaly for the strongest new report, or None.

    Only reports strictly newer than `cutoff` count (index 0 never does: it has no
    preceding report to predict from). Votes are recorded for the single report picked,
    so "two detectors agree" means they agree on the same report, not on the window.
    """
    observations = window_observations(window, errors)
    # Transitions the model cannot fairly score (sentinel coordinates, long silences, bad time
    # steps, antimeridian crossings) never vote; see features/quality.py.
    unscorable = transition_reasons(window.positions, window.timestamps)
    best: tuple[tuple[int, float, int], int, frozenset[str]] | None = None
    for index in range(1, len(observations)):
        if window.timestamps[index] <= cutoff:
            continue
        if unscorable[index] is not None:
            continue
        votes = detector_votes(observations[index], thresholds)
        if len(votes) < min_votes or not votes:
            continue
        rank = (len(votes), float(observations[index].prediction_error or 0.0), index)
        if best is None or rank > best[0]:
            best = (rank, index, votes)
    if best is None:
        return None
    _, index, votes = best
    latitude, longitude = window.positions[index]
    return FlaggedAnomaly(
        mmsi=window.mmsi,
        flagged_at=window.timestamps[index],
        anomaly_score=float(observations[index].prediction_error or 0.0),
        anomaly_type="+".join(sorted(votes)),
        latitude=float(latitude),
        longitude=float(longitude),
        detector_votes=votes,
        window_start=window.timestamps[0],
        window_end=window.timestamps[-1],
        tier="B" if threshold_b is not None and VOTE_PREDICTION_ERROR in votes and float(observations[index].prediction_error or 0.0) >= threshold_b else "A",
        threshold_a=thresholds.prediction_error,
    )


ScoreErrors = Callable[[FeatureWindow], np.ndarray]
InvestigateFn = Callable[[FlaggedAnomaly, Mapping[str, Tool], Persist], Awaitable[InvestigationResult]]


class LiveScorer:
    def __init__(
        self,
        store: Store,
        score_errors: ScoreErrors,
        tools: Mapping[str, Tool],
        persist: Persist,
        config: ScorerConfig,
        investigate_fn: InvestigateFn = investigate,  # type: ignore[assignment]
        threshold_source: ThresholdSource | None = None,
        model_version: str | None = None,
        after_persist: AfterPersist | None = None,
        retune: Retune | None = None,
        retune_interval_seconds: float = DEFAULT_RETUNE_INTERVAL_SECONDS,
    ) -> None:
        self._store = store
        self._score_errors = score_errors
        self._tools = tools
        self._persist = persist
        self._config = config
        self._investigate = investigate_fn
        # With no threshold source the scorer is static: config.thresholds.prediction_error is
        # threshold A and there is no tier B. With one, thresholds come from threshold_config
        # every cycle, and until a row exists the scorer only observes (see poll_once).
        self._threshold_source = threshold_source
        self._model_version = model_version
        self._after_persist = after_persist
        self._retune = retune
        self._retune_interval = retune_interval_seconds
        self._last_retune: datetime | None = None
        self._observing_logged = False
        self._watermark: datetime | None = None
        self._last_scored: dict[int, datetime] = {}
        self._cooldown: dict[int, datetime] = {}
        # Candidates skipped by the per-cycle cap must remain eligible for a later
        # cycle. Keep this in memory only: a process restart naturally starts from
        # the database watermark and will score newly observed reports again.
        self._deferred: dict[int, FlaggedAnomaly] = {}

    async def _is_debounced(self, mmsi: int, now: datetime) -> bool:
        window = timedelta(hours=self._config.debounce_hours)
        last = self._cooldown.get(mmsi)
        if last is not None and now - last < window:
            return True
        return await self._store.has_open_incident(mmsi, now - window)

    async def _maybe_retune(self, now: datetime) -> None:
        """Ask the threshold agent to look again: on a schedule, and right after a model change."""
        if self._retune is None:
            return
        due = self._last_retune is None or (now - self._last_retune).total_seconds() >= self._retune_interval
        if not due and self._threshold_source is not None:
            active = await self._threshold_source()
            due = active is None or active.model_version != self._model_version
            if due and self._last_retune is not None and (now - self._last_retune).total_seconds() < 300:
                due = False  # a model-change retune that kept the thresholds must not run every cycle
        if not due:
            return
        self._last_retune = now
        try:
            await self._retune()
        except Exception:  # noqa: BLE001 - keep scoring with the thresholds already in force
            logger.exception("threshold retune failed")

    async def poll_once(self) -> CycleSummary:
        config = self._config
        summary = CycleSummary()
        cycle_started = await self._store.db_now()
        since = self._watermark or cycle_started - timedelta(minutes=config.initial_lookback_minutes)
        not_before = cycle_started - timedelta(hours=config.window_max_age_hours)

        thresholds, threshold_b, observing = config.thresholds, None, False
        if self._threshold_source is not None:
            await self._maybe_retune(cycle_started)
            active = await self._threshold_source()
            if active is None:
                observing = True
                summary.observing_only = True
            else:
                thresholds = replace(config.thresholds, prediction_error=active.a)
                threshold_b = active.b
        score_samples: list[np.ndarray] = []
        mmsis = await self._store.active_mmsis(since)
        summary.vessels_seen = len(mmsis)
        candidates_by_mmsi: dict[int, FlaggedAnomaly] = dict(self._deferred)
        self._deferred.clear()
        for mmsi in mmsis:
            rows = await self._store.recent_reports(mmsi, not_before, WINDOW_LENGTH)
            if len(rows) < WINDOW_LENGTH:
                summary.short_history += 1
                continue
            window = window_rows(rows)[0]
            cutoff = max(since, self._last_scored.get(mmsi, since))
            if window.timestamps[-1] <= cutoff:
                continue  # nothing new since this vessel was last scored
            errors = await asyncio.to_thread(self._score_errors, window)
            summary.windows_scored += 1
            self._last_scored[mmsi] = window.timestamps[-1]
            skipped = transition_reasons(window.positions, window.timestamps)
            fresh = [i for i in range(1, len(skipped)) if window.timestamps[i] > cutoff]
            summary.skipped_unscorable += sum(1 for i in fresh if skipped[i] is not None)
            score_samples.append(np.array([errors[i] for i in fresh if skipped[i] is None], dtype=np.float64))
            if observing:
                continue  # no thresholds yet: score and record, flag nothing
            anomaly = evaluate_window(window, errors, cutoff, thresholds, config.min_votes, threshold_b)
            if anomaly is not None:
                previous = candidates_by_mmsi.get(anomaly.mmsi)
                if previous is None or (
                    len(anomaly.detector_votes), anomaly.anomaly_score, anomaly.flagged_at
                ) > (
                    len(previous.detector_votes), previous.anomaly_score, previous.flagged_at
                ):
                    candidates_by_mmsi[anomaly.mmsi] = anomaly

        # Strongest evidence first, so the per-cycle cap drops the weakest flags.
        candidates = sorted(
            candidates_by_mmsi.values(),
            key=lambda a: (len(a.detector_votes), a.anomaly_score, a.flagged_at),
            reverse=True,
        )
        for anomaly in candidates:
            summary.flagged += 1
            if anomaly.tier == "B":
                summary.flagged_b += 1
            else:
                summary.flagged_a += 1
            if await self._is_debounced(anomaly.mmsi, cycle_started):
                summary.debounced += 1
                continue
            if summary.investigated + summary.failed >= config.max_investigations_per_cycle:
                summary.deferred += 1
                self._deferred[anomaly.mmsi] = anomaly
                logger.warning("deferred flag mmsi=%s votes=%s (per-cycle cap reached)", anomaly.mmsi, sorted(anomaly.detector_votes))
                continue
            self._cooldown[anomaly.mmsi] = cycle_started
            try:
                result = await self._investigate(anomaly, self._tools, self._persist)
            except Exception:
                summary.failed += 1
                logger.exception("investigation failed mmsi=%s", anomaly.mmsi)
                continue
            summary.investigated += 1
            logger.info(
                "investigated mmsi=%s tier=%s votes=%s score=%.6f hypothesis=%s confidence=%.2f",
                anomaly.mmsi, anomaly.tier, sorted(anomaly.detector_votes), anomaly.anomaly_score, result.hypothesis, result.confidence,
            )
            if self._after_persist is not None:
                try:
                    await self._after_persist(anomaly, result)
                except Exception:  # noqa: BLE001 - the incident is already stored; follow-up agents must not undo that
                    logger.exception("follow-up agents failed for incident %s", result.incident_id)

        scored_errors = np.concatenate(score_samples) if score_samples else np.array([], dtype=np.float64)
        if scored_errors.size or summary.skipped_unscorable:
            try:
                await self._store.record_stats(
                    self._model_version, int(scored_errors.size), summary.skipped_unscorable,
                    summary.flagged_a, summary.flagged_b, histogram_counts(scored_errors),
                )
            except Exception:  # noqa: BLE001 - stats are for the threshold agent, never a reason to stop scoring
                logger.exception("could not record scoring stats")
        if observing and not self._observing_logged:
            logger.warning("no alert thresholds set yet: scoring and recording stats only, flagging nothing until the threshold agent (or `thresholds.py set`) sets them")
            self._observing_logged = True
        elif not observing:
            self._observing_logged = False

        # Advance only after a full pass: an exception above rescans the same range.
        self._watermark = cycle_started - timedelta(seconds=config.overlap_seconds)
        self._last_scored = {m: t for m, t in self._last_scored.items() if t > self._watermark}
        self._cooldown = {m: t for m, t in self._cooldown.items() if cycle_started - t < timedelta(hours=config.debounce_hours)}
        return summary

    async def run_forever(self) -> None:
        while True:
            try:
                summary = await self.poll_once()
                logger.info("cycle: %s", summary)
            except Exception:
                logger.exception("poll cycle failed; retrying next interval")
            await asyncio.sleep(self._config.poll_interval_seconds)


def build_tools(db: Any, pattern_predict: Predict | None = None) -> dict[str, Tool]:
    """The existing agent tools, bound to a database handle. No new tool logic here.

    `pattern_classifier` is always registered so every persisted incident records whether the
    Laya vote was available; with `pattern_predict=None` it is the neutral stub and changes
    nothing about the investigation (agent/tools/pattern_classifier.py).
    """

    async def track_history(anomaly: FlaggedAnomaly) -> dict[str, Any]:
        return await get_track_history(db, anomaly.mmsi, anomaly.flagged_at)

    async def jamming_zones(anomaly: FlaggedAnomaly) -> dict[str, Any]:
        return await check_jamming_zones(db, anomaly.latitude, anomaly.longitude, anomaly.flagged_at)

    async def incident_history(anomaly: FlaggedAnomaly) -> dict[str, Any]:
        return await find_similar_incidents(db, anomaly.mmsi, anomaly.anomaly_type)

    return {
        "track_history": track_history,
        "jamming_zones": jamming_zones,
        "incident_history": incident_history,
        "pattern_classifier": build_pattern_classifier(track_history, pattern_predict),
    }


def build_persist(db: Any) -> Persist:
    async def persist(row: dict[str, Any]) -> None:
        await persist_incident(db, row)
    return persist


def load_model_scorer(checkpoint_path: Path, device_name: str | None) -> tuple[ScoreErrors, dict]:
    """Load the BiLSTM once. torch is imported here, not at module import, so tests and
    tooling that never score a real window don't need it installed."""
    import torch
    from models.bilstm.infer import prediction_errors
    from models.bilstm.model import BiLSTMNextDelta

    # Online scoring is one small window at a time: CPU by default, no GPU needed.
    device = torch.device(device_name or "cpu")
    checkpoint = torch.load(checkpoint_path, map_location=device)
    model = BiLSTMNextDelta(N_FEATURES).to(device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    return (lambda window: prediction_errors(model, window)), checkpoint


async def report_retention_loop(pool: Any, interval_seconds: float) -> None:
    """Clear report text older than 24 hours. Incidents, evidence and verdicts are kept forever."""
    while True:
        try:
            cleared = await purge_expired_reports(pool)
            if cleared:
                logger.info("report retention: cleared %d expired report(s)", cleared)
        except Exception:  # noqa: BLE001 - retention must never take the scorer down
            logger.exception("report retention failed")
        await asyncio.sleep(interval_seconds)


def model_version_of(checkpoint_path: Path, checkpoint: dict, laya_model: str | None) -> str:
    """Identity of the models doing the scoring. It changes when a checkpoint is replaced or the
    Laya model is re-fine-tuned, which is what tells the threshold agent to look again."""
    parts = [f"{checkpoint_path.name}:e{checkpoint.get('epoch')}:{int(checkpoint_path.stat().st_mtime)}"]
    if laya_model:
        laya_path = Path(laya_model)
        parts.append(f"laya:{laya_path.name}:{int(laya_path.stat().st_mtime) if laya_path.exists() else 'missing'}")
    return "+".join(parts)


def build_llm_client() -> Any | None:
    """A Groq client when GROQ_API_KEY is set, else None (agents then use their baselines)."""
    if not os.environ.get("GROQ_API_KEY"):
        return None
    try:
        from groq import AsyncGroq
        return AsyncGroq()
    except Exception as error:  # noqa: BLE001
        logger.warning("llm missing: %s (agents run on their deterministic baselines)", type(error).__name__)
        return None


async def _serve(args: argparse.Namespace) -> int:
    import asyncpg
    from dotenv import load_dotenv

    # Local development keeps credentials in the repository's ignored .env. Do not
    # override an environment value supplied by Docker, CI, or the process manager.
    load_dotenv(_REPO_ROOT / ".env")
    checkpoint_path = Path(args.checkpoint)
    score_errors, checkpoint = load_model_scorer(checkpoint_path, args.device)
    version = model_version_of(checkpoint_path, checkpoint, args.laya_model)
    logger.info("loaded model_version=%s", version)

    config = ScorerConfig(
        thresholds=DetectorThresholds(
            prediction_error=float(OPERATING_THRESHOLD) if args.static_threshold else 1.0,  # replaced each cycle unless --static-threshold
            freeze_replay=args.freeze_replay_threshold,
            speed_jump=args.speed_jump_threshold,
        ),
        min_votes=args.min_votes,
        debounce_hours=args.debounce_hours,
        max_investigations_per_cycle=args.max_investigations_per_cycle,
        initial_lookback_minutes=args.initial_lookback_minutes,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    pattern_predict: Predict | None = None
    if args.laya_model:
        try:
            pattern_predict = load_laya_predictor(args.laya_model, args.device)
            logger.info("loaded Laya pattern classifier from %s", args.laya_model)
        except Exception as error:  # ImportError if laya is not installed, or a bad checkpoint path
            logger.warning("laya missing: could not load %s (%s); scoring continues without it", args.laya_model, type(error).__name__)
    else:
        logger.warning("laya missing: GHAST_LAYA_MODEL not set; scoring continues without it")

    pool = await asyncpg.create_pool(args.dsn, min_size=1, max_size=3)
    try:
        client = build_llm_client()
        if client is None:
            logger.warning("llm missing: GROQ_API_KEY not set; agents use their deterministic baselines")
        model = os.environ.get("GHAST_AGENT_MODEL") or os.environ.get("GHAST_REPORT_MODEL") or "openai/gpt-oss-120b"
        budgets = Budgets(args.budget_a, args.budget_b)
        threshold_agent = build_threshold_agent(pool, client, model, version, budgets, _REPO_ROOT / "ml" / "reports")

        async def retune() -> None:
            await retune_thresholds(pool, threshold_agent, version, budgets)

        dynamic = not args.static_threshold
        thresholds_now = PostgresThresholds(pool)

        async def threshold_b_now() -> float | None:
            active = await thresholds_now()
            return active.b if active else None

        pipeline = IncidentPipeline(pool, client, model, threshold_b_now)
        scorer = LiveScorer(
            PostgresStore(pool), score_errors, build_tools(pool, pattern_predict), build_persist(pool), config,
            threshold_source=PostgresThresholds(pool) if dynamic else None,
            model_version=version,
            after_persist=pipeline,
            retune=retune if dynamic else None,
            retune_interval_seconds=args.retune_interval_seconds,
        )
        if args.once:
            print(f"cycle: {await scorer.poll_once()}")
            return 0
        retention = asyncio.create_task(report_retention_loop(pool, args.retention_interval_seconds))
        try:
            await scorer.run_forever()
        finally:
            retention.cancel()
        return 0
    finally:
        await pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=os.environ.get("POSTGRES_DSN"), help="Defaults to $POSTGRES_DSN.")
    parser.add_argument("--checkpoint", default=os.environ.get("GHAST_CHECKPOINT"), help="Path to a .pt checkpoint. Defaults to $GHAST_CHECKPOINT.")
    parser.add_argument("--poll-interval-seconds", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--debounce-hours", type=float, default=DEFAULT_DEBOUNCE_HOURS, help="Skip vessels with an unresolved incident newer than this.")
    parser.add_argument("--min-votes", type=int, default=DEFAULT_MIN_VOTES, help="Detectors that must agree on one report to flag it; 2 trades recall for precision.")
    parser.add_argument("--freeze-replay-threshold", type=float, default=FREEZE_DISPLACEMENT_EPSILON_KNOTS)
    parser.add_argument("--speed-jump-threshold", type=float, default=None, help="Off unless set; no swept value is on record yet.")
    parser.add_argument("--max-investigations-per-cycle", type=int, default=DEFAULT_MAX_INVESTIGATIONS_PER_CYCLE)
    parser.add_argument("--initial-lookback-minutes", type=float, default=DEFAULT_INITIAL_LOOKBACK_MINUTES, help="How far back the first cycle looks for new reports.")
    parser.add_argument("--laya-model", default=os.environ.get("GHAST_LAYA_MODEL"), help="Path to a fine-tuned Laya checkpoint directory. Defaults to $GHAST_LAYA_MODEL; unset means a neutral stub.")
    parser.add_argument("--device", choices=("cpu", "cuda"), help="Defaults to cpu.")
    parser.add_argument("--static-threshold", action="store_true", help="Legacy mode: use models.bilstm.threshold.OPERATING_THRESHOLD as the only threshold, no tiers, no agent. Not recommended: that constant is a leftover from injected data.")
    parser.add_argument("--budget-a", type=float, default=float(os.environ.get("GHAST_BUDGET_A", DEFAULT_BUDGET_A)), help="Alert budget for threshold A: share of scoreable reports that may reach the console. Placeholder default; the owner's call.")
    parser.add_argument("--budget-b", type=float, default=float(os.environ.get("GHAST_BUDGET_B", DEFAULT_BUDGET_B)), help="Alert budget for threshold B: share of reports that are reported automatically.")
    parser.add_argument("--retune-interval-seconds", type=float, default=DEFAULT_RETUNE_INTERVAL_SECONDS, help="How often the threshold agent looks again (it also looks right after a model change).")
    parser.add_argument("--retention-interval-seconds", type=float, default=900.0, help="How often expired (24h old) reports are cleared.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.static_threshold and OPERATING_THRESHOLD is None:
        print("OPERATING_THRESHOLD is None (models/bilstm/threshold.py): refusing --static-threshold without one.", file=sys.stderr)
        return 2
    if not 0 < args.budget_b < args.budget_a < 1:
        parser.error("budgets must satisfy 0 < budget-b < budget-a < 1")
    if not args.dsn:
        parser.error("--dsn is required (or set POSTGRES_DSN)")
    if not args.checkpoint:
        parser.error("--checkpoint is required (or set GHAST_CHECKPOINT)")
    if args.min_votes < 1:
        parser.error("--min-votes must be at least 1")
    return asyncio.run(_serve(args))


if __name__ == "__main__":
    raise SystemExit(main())
