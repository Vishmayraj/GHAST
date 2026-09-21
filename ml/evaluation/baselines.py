"""Trivial baseline detectors.

These exist to prove the evaluation harness works end-to-end before
ml/models/bilstm/ has anything trained (Weeks 7-10 of
ImplementationPlans/Sem5IP.md come after this). Neither is meant to be
competitive - they're a known-shape signal to run precision/recall/F1
against so the harness itself is validated first.

A "detector" here is just a callable: AISObservation -> float score,
higher meaning more likely spoofed. harness.py turns a score into a
boolean prediction with a threshold.
"""

from __future__ import annotations

from collections.abc import Callable

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
