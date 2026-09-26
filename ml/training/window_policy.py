"""Choose source-safe initial and rolling windows for live AIS model training."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

TRAINING_WINDOW_DAYS = 14
INITIAL_RUN_WAIT_DAYS = 15


@dataclass(frozen=True)
class TrainingWindowDecision:
    """A ready decision carries exact source-scoped bounds; no dates are guessed."""

    ready: bool
    start: datetime | None
    end: datetime | None
    reason: str


def initial_live_window(first_live_report: datetime | None, latest_live_report: datetime | None, now: datetime) -> TrainingWindowDecision:
    """Gate the first run on observed live coverage, not a misleading recent-date filter."""
    if first_live_report is None or latest_live_report is None:
        return TrainingWindowDecision(False, None, None, "No live AIS reports have been received.")
    end = first_live_report + timedelta(days=TRAINING_WINDOW_DAYS)
    if latest_live_report < end:
        return TrainingWindowDecision(False, None, None, "Live AIS coverage has not reached 14 complete days.")
    if now < first_live_report + timedelta(days=INITIAL_RUN_WAIT_DAYS):
        return TrainingWindowDecision(False, None, None, "Initial 15-day operational wait has not elapsed.")
    return TrainingWindowDecision(True, first_live_report, end, "First complete live training window.")


def rolling_live_window(first_live_report: datetime | None, latest_live_report: datetime | None) -> TrainingWindowDecision:
    """Use the latest complete 14-day live range after the first trained model exists."""
    if first_live_report is None or latest_live_report is None:
        return TrainingWindowDecision(False, None, None, "No live AIS reports have been received.")
    start = latest_live_report - timedelta(days=TRAINING_WINDOW_DAYS)
    if first_live_report > start:
        return TrainingWindowDecision(False, None, None, "Live AIS coverage has not reached 14 complete days.")
    return TrainingWindowDecision(True, start, latest_live_report, "Rolling live training window.")
