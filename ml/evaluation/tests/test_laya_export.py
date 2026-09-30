"""laya_export core logic, on synthetic windows: no database, no torch."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np

from evaluation.laya_export import Quota, assign_case, collect_rows, make_row
from features.extract import COG_INDEX, N_FEATURES, SOG_INDEX
from features.pipeline import FeatureWindow
from features.summary import NORMAL_LABEL, PATTERN_LABELS

START = datetime(2026, 9, 1, tzinfo=timezone.utc)


def window(mmsi: int, day: int = 0) -> FeatureWindow:
    features = np.zeros((20, N_FEATURES), dtype=np.float32)
    features[:, SOG_INDEX] = 10.0
    features[:, COG_INDEX] = 0.0
    positions = np.array([(51.0 + i * 0.0028, 4.0) for i in range(20)], dtype=np.float64)
    stamps = tuple(START + timedelta(days=day, minutes=i) for i in range(20))
    return FeatureWindow(mmsi, stamps[0], stamps[-1], features, positions, stamps)


def test_row_matches_laya_notebook_schema() -> None:
    row = make_row(window(1), "teleport_jump", 0.5, 7, "train")
    state, questions, gold = json.loads(row["state"]), json.loads(row["questions"]), json.loads(row["gold"])
    assert isinstance(state, str) and "implied_speed_knots" in state
    assert list(questions["pattern"]["criteria"]) == list(PATTERN_LABELS)
    assert gold["pattern"]["label"] == "teleport_jump"
    assert sum(gold["pattern"]["probabilities"].values()) == 1.0


def test_control_window_is_left_clean_and_labelled_normal() -> None:
    row = make_row(window(1), NORMAL_LABEL, 0.5, 7, "train")
    assert row["pattern"] is None
    assert json.loads(row["gold"])["pattern"]["label"] == NORMAL_LABEL
    assert "steps_claiming_speed_but_not_moving: 0" in json.loads(row["state"])


def test_assignment_is_deterministic_and_roughly_even() -> None:
    windows = [window(m, d) for m in range(1, 41) for d in range(5)]
    assert assign_case(windows[3], 0) == assign_case(windows[3], 0)
    counts = {label: 0 for label in PATTERN_LABELS}
    for w in windows:
        counts[assign_case(w, 0)[0]] += 1
    assert min(counts.values()) > 0.5 * (len(windows) / len(PATTERN_LABELS))


def test_quota_caps_each_class_and_split_and_keeps_vessels_apart() -> None:
    windows = [window(m, d) for m in range(1, 200) for d in range(4)]
    quota = Quota(per_class_train=5, per_class_holdout=2)
    holdout_vessel = lambda mmsi: mmsi % 5 == 0  # noqa: E731
    rows = collect_rows(windows, quota, seed=0, sample_permille=1000, is_holdout_vessel=holdout_vessel)

    assert quota.done
    assert len(rows) == 5 * 5 + 5 * 2
    train_vessels = {r["mmsi"] for r in rows if r["split"] == "train"}
    holdout_vessels = {r["mmsi"] for r in rows if r["split"] == "holdout"}
    assert not train_vessels & holdout_vessels
    for split, cap in (("train", 5), ("holdout", 2)):
        per_label = {label: sum(1 for r in rows if r["split"] == split and json.loads(r["gold"])["pattern"]["label"] == label) for label in PATTERN_LABELS}
        assert set(per_label.values()) == {cap}


def test_thinning_drops_windows() -> None:
    windows = [window(m) for m in range(1, 300)]
    rows = collect_rows(windows, Quota(10**6, 10**6), seed=0, sample_permille=100, is_holdout_vessel=lambda m: False)
    assert 5 < len(rows) < 80
