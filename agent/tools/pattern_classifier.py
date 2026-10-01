"""Laya pattern classifier: a fourth, independent evidence source for `investigate()`.

The model is a Laya `choice` head asked which of five classes a flagged 20-report window
looks like: normal_track, teleport_jump, gradual_drift, freeze_replay, impossible_kinematics.
Its input is the text from features.summary.summarize_rows, the exact function the
training export (ml/evaluation/laya_export.py) uses. The checkpoint currently in use was
fine-tuned on injected windows that have since been removed from the repo; retraining is
meant to use analyst-labelled real windows (ImplementationPlans/04_Laya_Real_Labels.md).

This is the one external dependency in the agent, so it is isolated on purpose:

* No model configured (`GHAST_LAYA_MODEL` unset), or Laya not installed, or the model
  raises: the tool returns `{"available": False, "reason": ...}` and the investigation
  carries on exactly as it did before this tool existed. It never raises into `investigate`.
* The `laya` import happens inside `load_laya_predictor`, never at module import, so the
  agent's test suite and CI need neither Laya, transformers, nor torch.
* Until a checkpoint has been fine-tuned AND evaluated on analyst-labelled real windows,
  nothing here has been measured on real spoofing. `form_hypothesis` therefore only lets this vote lift or
  apply a confidence cap; it never decides a hypothesis on its own. See state_machine.py.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from features.summary import QUESTION_ID, pattern_questions, summarize_rows

logger = logging.getLogger("ghast.agent.pattern_classifier")

# Same length the model and detectors were trained and scored on (features.pipeline.WINDOW_LENGTH,
# repeated here so the agent does not import the asyncpg-backed pipeline module).
WINDOW_LENGTH = 20

# label + probabilities for one summary text, e.g. {"label": "freeze_replay", "probabilities": {...}}
Predict = Callable[[str], Mapping[str, Any]]
GetTrack = Callable[[Any], Awaitable[Mapping[str, Any]]]


def unavailable(reason: str) -> dict[str, Any]:
    """Neutral result: `form_hypothesis` treats `available: False` as no evidence either way."""
    return {"available": False, "pattern": None, "confidence": None, "probabilities": None, "reason": reason}


def recent_window(positions: Sequence[Mapping[str, Any]], flagged_at: Any, length: int = WINDOW_LENGTH) -> list[Mapping[str, Any]]:
    """The `length` reports ending at (and including) the flagged one, oldest first."""
    upto = [row for row in positions if row["received_at"] <= flagged_at]
    upto.sort(key=lambda row: row["received_at"])
    return upto[-length:]


def load_laya_predictor(model_path: str, device: str | None = None) -> Predict:
    """Load a fine-tuned Laya checkpoint once and return a `Predict` callable.

    `model_path` is a local directory (or Hub id) holding what Laya's fine-tuning notebook
    writes: model.safetensors, encoder/, tokenizer/, rl_agent_config.json. Requires
    `pip install laya` (see scoring/requirements-laya.txt); raises ImportError otherwise,
    which `build_pattern_classifier` callers should catch and turn into the stub.
    """
    import laya  # noqa: PLC0415 - deliberately lazy, see module docstring

    agent = laya.load(model_path, device=device or "cpu")
    questions = pattern_questions()

    def predict(state: str) -> dict[str, Any]:
        answer = agent.predict(state, questions)["answers"][QUESTION_ID]
        probabilities = {str(key): float(value) for key, value in (answer.get("probabilities") or {}).items()}
        confidence = answer.get("answer_confidence")
        if confidence is None:
            confidence = probabilities.get(answer["choice"])
        return {"label": answer["choice"], "confidence": confidence, "probabilities": probabilities}

    return predict


def build_pattern_classifier(get_track: GetTrack, predict: Predict | None):
    """Return an agent `Tool`. With `predict=None` it is the documented neutral stub."""

    async def pattern_classifier(anomaly: Any) -> dict[str, Any]:
        if predict is None:
            return unavailable("no Laya model configured (set GHAST_LAYA_MODEL)")
        try:
            track = await get_track(anomaly)
            window = recent_window(track.get("positions") or [], anomaly.flagged_at)
            if len(window) < WINDOW_LENGTH:
                return unavailable(f"only {len(window)} reports up to the flag, need {WINDOW_LENGTH}")
            result = await asyncio.to_thread(lambda: predict(summarize_rows(window)))
        except Exception as error:  # noqa: BLE001 - an optional tool must never fail the investigation
            logger.exception("pattern classifier failed mmsi=%s", getattr(anomaly, "mmsi", None))
            return unavailable(f"classifier error: {type(error).__name__}")
        probabilities = dict(result.get("probabilities") or {})
        confidence = result.get("confidence")
        return {
            "available": True,
            "pattern": result["label"],
            "confidence": None if confidence is None else float(confidence),
            "probabilities": probabilities,
            "reason": None,
        }

    return pattern_classifier
