"""Export a class-balanced Laya fine-tuning set from the injector's own pattern labels.

BigPass plan section 6. Each row is one 20-report window, either left clean (label
`normal_track`) or altered by one of features.inject's four injectors, turned into the
row schema Laya's fine-tuning notebook reads (`state`, `questions`, `gold`, each a JSON
string). The text comes from features.summary, the same function the live
agent/tools/pattern_classifier.py uses, so training and serving see identical input.

Why this is not "sample down the 6.4M scored rows": classes are assigned per window from a
hash, so they are near-even by construction and quotas trim the rest. That sidesteps the
injector's CONTROL_FRACTION skew, and it never holds more than one window in memory.

Splits are by vessel with the same rule the BiLSTM used (training.dataset_cache.
is_validation_vessel), so Laya's held-out rows come from vessels the BiLSTM never trained
on and no vessel is on both sides.

Usage (from ml/, DB reachable; nothing here needs torch or a checkpoint):
    python -m evaluation.laya_export --dsn $POSTGRES_DSN --source historical \\
        --start 2026-04-01 --end 2026-04-15 --out-dir ../data/laya
Writes train.jsonl, holdout.jsonl and manifest.json into --out-dir.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from features.inject import INJECTORS
from features.summary import (
    NORMAL_LABEL, PATTERN_LABELS, QUESTION_ID, label_for_pattern, pattern_questions,
    summarize_rows, window_to_rows,
)

SEVERITIES = (0.25, 0.5, 0.75)
DEFAULT_PER_CLASS_TRAIN = 1500
DEFAULT_PER_CLASS_HOLDOUT = 300
DEFAULT_SAMPLE_PERMILLE = 50


def _hash_int(*parts: object, seed: int) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in (seed, *parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def assign_case(window: Any, seed: int) -> tuple[str, float, int]:
    """Deterministically pick (label, severity, injector_seed) for one clean window."""
    h = _hash_int(window.mmsi, window.window_start.isoformat(), seed=seed)
    label = PATTERN_LABELS[h % len(PATTERN_LABELS)]
    severity = SEVERITIES[(h >> 8) % len(SEVERITIES)]
    return label, severity, (h >> 16) % (2**32 - 1)


def keep_window(window: Any, seed: int, sample_permille: int) -> bool:
    """Order-independent thinning, so streaming in MMSI order doesn't bias which vessels we see."""
    return _hash_int("keep", window.mmsi, window.window_start.isoformat(), seed=seed) % 1000 < sample_permille


def make_row(window: Any, label: str, severity: float, injector_seed: int, split: str) -> dict[str, Any]:
    """One Laya training row from a clean window plus the label to inject into it."""
    if label == NORMAL_LABEL:
        altered = window
    else:
        altered, _ = INJECTORS[label](window, severity, injector_seed)
    gold = {QUESTION_ID: {
        "label": label,
        "probabilities": {key: 1.0 if key == label else 0.0 for key in PATTERN_LABELS},
    }}
    return {
        "id": f"{split}-{window.mmsi}-{window.window_start.isoformat()}",
        "split": split,
        "mmsi": window.mmsi,
        "pattern": None if label == NORMAL_LABEL else label,
        "severity": None if label == NORMAL_LABEL else severity,
        "state": json.dumps(summarize_rows(window_to_rows(altered))),
        "questions": json.dumps(pattern_questions()),
        "gold": json.dumps(gold),
    }


class Quota:
    """Per-split, per-class caps; `done` once every cap is met."""

    def __init__(self, per_class_train: int, per_class_holdout: int) -> None:
        self._caps = {"train": per_class_train, "holdout": per_class_holdout}
        self.counts: dict[str, Counter[str]] = {"train": Counter(), "holdout": Counter()}

    def wants(self, split: str, label: str) -> bool:
        return self.counts[split][label] < self._caps[split]

    def add(self, split: str, label: str) -> None:
        self.counts[split][label] += 1

    @property
    def done(self) -> bool:
        return all(self.counts[s][label] >= cap for s, cap in self._caps.items() for label in PATTERN_LABELS)


def collect_rows(windows: Iterable[Any], quota: Quota, seed: int, sample_permille: int, is_holdout_vessel) -> list[dict[str, Any]]:
    """Synchronous core: turn clean windows into quota-limited rows. Pure, so it is unit-tested."""
    rows: list[dict[str, Any]] = []
    for window in windows:
        if quota.done:
            break
        if not keep_window(window, seed, sample_permille):
            continue
        label, severity, injector_seed = assign_case(window, seed)
        split = "holdout" if is_holdout_vessel(window.mmsi) else "train"
        if not quota.wants(split, label):
            continue
        rows.append(make_row(window, label, severity, injector_seed, split))
        quota.add(split, label)
    return rows


async def _export(args: argparse.Namespace) -> int:
    from features.pipeline import stream_feature_windows
    from training.dataset_cache import is_validation_vessel

    quota = Quota(args.per_class_train, args.per_class_holdout)
    rows_by_split: dict[str, list[dict[str, Any]]] = {"train": [], "holdout": []}
    windows: list[Any] = []
    async for window in stream_feature_windows(
        args.dsn, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end), args.source,
        max_windows=args.max_windows,
    ):
        windows.append(window)
        # Bounded: the list only ever holds one small batch of clean windows before it is
        # drained into (much smaller, quota-capped) rows.
        if len(windows) >= 2000:
            _drain(windows, quota, args, is_validation_vessel, rows_by_split)
            windows.clear()
            if quota.done:
                break
    _drain(windows, quota, args, is_validation_vessel, rows_by_split)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for split in ("train", "holdout"):
        with (out_dir / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows_by_split[split]:
                handle.write(json.dumps(row) + "\n")
    manifest = {
        "source": args.source, "start": args.start, "end": args.end, "seed": args.seed,
        "sample_permille": args.sample_permille, "labels": list(PATTERN_LABELS),
        "counts": {s: dict(quota.counts[s]) for s in quota.counts},
        "quota_met": quota.done,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    if not quota.done:
        print("quota not met: widen --start/--end or raise --sample-permille; class counts above are what was found")
    return 0


def _drain(
    windows: list[Any], quota: Quota, args: argparse.Namespace, is_validation_vessel,
    rows_by_split: dict[str, list[dict[str, Any]]],
) -> None:
    for row in collect_rows(windows, quota, args.seed, args.sample_permille, is_validation_vessel):
        rows_by_split[row["split"]].append(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--source", choices=("live", "historical"), default="historical")
    parser.add_argument("--start", required=True, help="ISO date/datetime")
    parser.add_argument("--end", required=True, help="ISO date/datetime")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--per-class-train", type=int, default=DEFAULT_PER_CLASS_TRAIN)
    parser.add_argument("--per-class-holdout", type=int, default=DEFAULT_PER_CLASS_HOLDOUT)
    parser.add_argument("--sample-permille", type=int, default=DEFAULT_SAMPLE_PERMILLE, help="Keep this many windows per 1000 before class assignment.")
    parser.add_argument("--max-windows", type=int, help="Development cap on windows read from the database.")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    return asyncio.run(_export(args))


if __name__ == "__main__":
    raise SystemExit(main())
