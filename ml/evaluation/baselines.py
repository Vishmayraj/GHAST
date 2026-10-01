"""Trivial baseline detectors.

These exist to prove the evaluation harness works end-to-end before
ml/models/bilstm/ has anything trained (Weeks 7-10 of
ImplementationPlans/old/Sem5IP.md come after this). Neither is meant to be
competitive - they're a known-shape signal to run precision/recall/F1
against so the harness itself is validated first.

A "detector" here is just a callable: AISObservation -> float score,
higher meaning more likely spoofed. harness.py turns a score into a
boolean prediction with a threshold.
"""

from __future__ import annotations

from collections.abc import Callable

from features.extract import FREEZE_DISPLACEMENT_EPSILON_KNOTS

from .datasets import AISObservation

Detector = Callable[[AISObservation], float]


def prediction_error_detector(observation: AISObservation) -> float:
    """Score = the dataset's own next-position prediction error.

    Only meaningful for sources that provide `prediction_error` (currently
    just gps_spoofing_mass, where it comes pre-computed from the original
    authors' own predictive model). This is conceptually the same signal
    ml/models/bilstm/ will eventually generate itself - a large gap
    between predicted and reported position - so it's a reasonable stand-in
    for "does the harness correctly reward a real spoofing signal" while
    no model of our own exists yet.
    """
    return observation.prediction_error if observation.prediction_error is not None else 0.0


def speed_jump_detector(observation: AISObservation) -> float:
    """Score = magnitude of acceleration (change in speed_calc).

    Doesn't depend on prediction_error, so it also works on our own live
    ingestion data (ingestion/normalizer/) once that has enough history to
    compute a delta, unlike prediction_error_detector which only makes
    sense against gps_spoofing_mass.
    """
    return abs(observation.acceleration) if observation.acceleration is not None else 0.0


def freeze_replay_detector(observation: AISObservation) -> float:
    """Score = how far a report's own claimed speed exceeds its actual, position-implied motion.

    prediction_error_detector's per-step L2 error can miss a frozen or replayed
    report (the followup run's own per-pattern breakdown found freeze/replay F1
    only 0.244) - a frozen position is not necessarily a large next-step
    prediction error, it just isn't a *new* position. This detector looks at the
    frozen-report signature instead: `sog` claiming ongoing movement
    while `implied_speed` (the position delta from the previous report, converted
    to knots) is at or below FREEZE_DISPLACEMENT_EPSILON_KNOTS, i.e. the vessel
    didn't actually go anywhere despite saying it was moving.

    Returns 0.0 (no signal) when either field is missing, or when the observed
    displacement is above the near-zero epsilon (ordinary motion, not a freeze).
    """
    if observation.sog is None or observation.implied_speed is None:
        return 0.0
    if observation.implied_speed < 0 or observation.implied_speed > FREEZE_DISPLACEMENT_EPSILON_KNOTS:
        # implied_speed < 0 is features.extract.MISSING_VALUE's sentinel (first
        # report in a window, or a non-positive elapsed time) - a real distance-
        # implied speed is never negative, so this can't be confused with a
        # genuine near-zero reading.
        return 0.0
    return max(0.0, observation.sog - observation.implied_speed)
