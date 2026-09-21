"""Precision/recall/F1 for binary spoofing predictions.

Stdlib only - deliberately no numpy/sklearn dependency for something this
small, so ml/evaluation/ doesn't need heavy deps just to score a model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfusionMatrix:
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int

    @property
    def total(self) -> int:
        return self.true_positive + self.false_positive + self.true_negative + self.false_negative


@dataclass(frozen=True)
class EvaluationMetrics:
    confusion: ConfusionMatrix
    precision: float
    recall: float
    f1: float
    accuracy: float


def confusion_matrix(y_true: list[bool], y_pred: list[bool]) -> ConfusionMatrix:
    if len(y_true) != len(y_pred):
        raise ValueError(f"y_true and y_pred must be the same length ({len(y_true)} != {len(y_pred)})")
    tp = fp = tn = fn = 0
    for actual, predicted in zip(y_true, y_pred):
        if actual and predicted:
            tp += 1
        elif not actual and predicted:
            fp += 1
        elif not actual and not predicted:
            tn += 1
        else:  # actual and not predicted
            fn += 1
    return ConfusionMatrix(true_positive=tp, false_positive=fp, true_negative=tn, false_negative=fn)


def precision_recall_f1(y_true: list[bool], y_pred: list[bool]) -> EvaluationMetrics:
    """Precision/recall/F1 treating "spoofed" (True) as the positive class.

    Every ratio is defined as 0.0 on a zero denominator (e.g. no positive
    predictions at all) rather than raising, since a harness run against
    a weak early baseline should still print a full, comparable result.
    """
    cm = confusion_matrix(y_true, y_pred)
    predicted_positive = cm.true_positive + cm.false_positive
    actual_positive = cm.true_positive + cm.false_negative

    precision = cm.true_positive / predicted_positive if predicted_positive else 0.0
    recall = cm.true_positive / actual_positive if actual_positive else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    accuracy = (cm.true_positive + cm.true_negative) / cm.total if cm.total else 0.0

    return EvaluationMetrics(confusion=cm, precision=precision, recall=recall, f1=f1, accuracy=accuracy)
