"""laya_export core logic on hand-built real-shaped windows: queue sampling, label validation,
the vessel split, and the eight-column row schema Laya's notebook loads. No database."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from evaluation.laya_export import (
    build_dataset, collect_queue, keep_window, load_labels, queue_row, training_row, window_id, write_dataset,
)
from features.extract import COG_INDEX, N_FEATURES, SOG_INDEX
from features.pipeline import FeatureWindow
from features.summary import NORMAL_LABEL, PATTERN_LABELS

START = datetime(2026, 9, 1, tzinfo=timezone.utc)
LAYA_COLUMNS = {"id", "split", "mmsi", "pattern", "severity", "state", "questions", "gold"}


def window(mmsi: int, offset_minutes: int = 0) -> FeatureWindow:
    features = np.zeros((20, N_FEATURES), dtype=np.float32)
    features[:, SOG_INDEX] = 10.0
    features[:, COG_INDEX] = 0.0
    positions = np.array([(51.0 + i * 0.0028, 4.0) for i in range(20)], dtype=np.float64)
    first = START + timedelta(minutes=offset_minutes)
    timestamps = tuple(first + timedelta(minutes=i) for i in range(20))
    return FeatureWindow(mmsi, timestamps[0], timestamps[-1], features, positions, timestamps)


def is_holdout(mmsi: int) -> bool:
    return mmsi % 5 == 0


def test_window_id_is_stable_and_unique_per_vessel_and_start() -> None:
    assert window_id(window(1)) == window_id(window(1))
    assert window_id(window(1)) != window_id(window(2))
    assert window_id(window(1)) != window_id(window(1, offset_minutes=60))


def test_queue_row_has_summary_and_no_answer() -> None:
    row = queue_row(window(7), "train", "live")
    assert "gold" not in row and "pattern" not in row
    assert json.loads(row["state"]).startswith("reports: 20")
    assert row["source"] == "live" and row["split"] == "train"
    with pytest.raises(ValueError):
        queue_row(window(7), "validation", "live")


def test_keep_window_is_deterministic_and_roughly_proportional() -> None:
    windows = [window(mmsi) for mmsi in range(2000)]
    kept = [w for w in windows if keep_window(w, seed=0, sample_permille=100)]
    assert kept == [w for w in windows if keep_window(w, seed=0, sample_permille=100)]
    assert 120 < len(kept) < 280
    assert not any(keep_window(w, seed=0, sample_permille=0) for w in windows)


def test_collect_queue_splits_by_vessel_and_respects_max_rows() -> None:
    windows = [window(mmsi) for mmsi in range(1, 400)]
    rows = collect_queue(windows, seed=0, sample_permille=1000, is_holdout_vessel=is_holdout, source="historical", max_rows=50)
    assert len(rows) == 50
    assert all(row["split"] == ("holdout" if is_holdout(row["mmsi"]) else "train") for row in rows)


def test_load_labels_validates(tmp_path) -> None:
    good = tmp_path / "good.jsonl"
    good.write_text('{"id": "a", "label": "teleport_jump"}\n\n{"id": "b", "label": "normal_track"}\n')
    assert load_labels(good) == {"a": "teleport_jump", "b": "normal_track"}
    for name, text in {
        "unknown.jsonl": '{"id": "a", "label": "spoofed"}',
        "missing.jsonl": '{"id": "a"}',
        "conflict.jsonl": '{"id": "a", "label": "teleport_jump"}\n{"id": "a", "label": "gradual_drift"}',
        "broken.jsonl": "not json",
    }.items():
        path = tmp_path / name
        path.write_text(text)
        with pytest.raises(ValueError):
            load_labels(path)


def test_training_row_uses_exactly_the_laya_columns() -> None:
    row = training_row(queue_row(window(3), "train", "live"), "freeze_replay")
    assert set(row) == LAYA_COLUMNS
    assert row["pattern"] == "freeze_replay" and row["severity"] is None
    gold = json.loads(row["gold"])["pattern"]
    assert gold["label"] == "freeze_replay"
    assert gold["probabilities"]["freeze_replay"] == 1.0 and sum(gold["probabilities"].values()) == 1.0
    assert set(gold["probabilities"]) == set(PATTERN_LABELS)


def test_normal_track_row_has_no_pattern() -> None:
    assert training_row(queue_row(window(3), "train", "live"), NORMAL_LABEL)["pattern"] is None


def test_build_dataset_ignores_unlabeled_and_reports_unknown_ids() -> None:
    queue = [queue_row(window(m), "holdout" if is_holdout(m) else "train", "live") for m in (1, 2, 5, 6)]
    labels = {queue[0]["id"]: "teleport_jump", queue[2]["id"]: NORMAL_LABEL, "ghost-id": "gradual_drift"}
    rows, manifest = build_dataset(queue, labels)
    assert [r["mmsi"] for r in rows["train"]] == [1] and [r["mmsi"] for r in rows["holdout"]] == [5]
    assert manifest["unlabeled_queue_rows"] == 2
    assert manifest["label_ids_not_in_queue"] == ["ghost-id"]
    assert manifest["counts"]["train"] == {"teleport_jump": 1}
    assert "synthetic" in manifest["label_source"]  # says there is none
    assert "holdout:teleport_jump" in manifest["classes_below_minimum"]["split_and_class"]


def test_build_dataset_refuses_a_vessel_in_both_splits() -> None:
    a, b = queue_row(window(1), "train", "live"), queue_row(window(1, 60), "holdout", "live")
    with pytest.raises(ValueError, match="both splits"):
        build_dataset([a, b], {a["id"]: NORMAL_LABEL, b["id"]: NORMAL_LABEL})


def test_write_dataset_round_trips(tmp_path) -> None:
    queue = [queue_row(window(m), "holdout" if is_holdout(m) else "train", "live") for m in (1, 5)]
    rows, manifest = build_dataset(queue, {q["id"]: NORMAL_LABEL for q in queue})
    write_dataset(rows, manifest, tmp_path / "out")
    train = [json.loads(line) for line in (tmp_path / "out" / "train.jsonl").read_text().splitlines()]
    assert len(train) == 1 and set(train[0]) == LAYA_COLUMNS
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["queue_rows"] == 2
