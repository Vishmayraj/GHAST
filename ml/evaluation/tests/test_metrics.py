import pytest

from evaluation.metrics import confusion_matrix, precision_recall_f1


def test_confusion_matrix_counts_all_four_quadrants():
    y_true = [True, True, False, False, True]
    y_pred = [True, False, False, True, True]
    cm = confusion_matrix(y_true, y_pred)
    assert cm.true_positive == 2  # indices 0, 4
    assert cm.false_negative == 1  # index 1
    assert cm.false_positive == 1  # index 3
    assert cm.true_negative == 1  # index 2
    assert cm.total == 5


def test_confusion_matrix_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        confusion_matrix([True, False], [True])


def test_precision_recall_f1_perfect_predictions():
    y_true = [True, False, True, False]
    y_pred = [True, False, True, False]
    metrics = precision_recall_f1(y_true, y_pred)
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.accuracy == 1.0


def test_precision_recall_f1_no_positive_predictions_is_zero_not_error():
    y_true = [True, True, False]
    y_pred = [False, False, False]
    metrics = precision_recall_f1(y_true, y_pred)
    assert metrics.precision == 0.0
    assert metrics.recall == 0.0
    assert metrics.f1 == 0.0


def test_precision_recall_f1_known_values():
    # 2 true positives, 1 false positive, 1 false negative, 1 true negative
    y_true = [True, True, True, False, False]
    y_pred = [True, True, False, True, False]
    metrics = precision_recall_f1(y_true, y_pred)
    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.recall == pytest.approx(2 / 3)
    assert metrics.f1 == pytest.approx(2 / 3)
