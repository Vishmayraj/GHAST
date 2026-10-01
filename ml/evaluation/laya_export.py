"""Build Laya training data from real AIS windows and human labels. No synthetic injection.

The Laya classifier answers "which pattern does this 20-report window show?". Real windows
in the database have no labels, so the data is made in two steps with a person in between:

1. `queue`: sample real 20-report windows from `vessel_position`, summarise each one with
   features.summary (the same text the live agent tool sends), and write a review queue.
   Rows have no `gold` answer.
2. A reviewer writes a labels file, one JSON object per line: {"id": "<queue id>", "label":
   "<one of features.summary.PATTERN_LABELS>"}. Unlabeled queue rows are ignored.
3. `build`: join queue and labels into train.jsonl, holdout.jsonl and manifest.json in the row
   schema Laya's fine-tuning notebook reads (`id, split, mmsi, pattern, severity, state,
   questions, gold`).

The split is by vessel with the BiLSTM's rule (training.dataset_cache.is_validation_vessel), so
no vessel is on both sides and holdout vessels were not seen by the BiLSTM in training.

Usage (from ml/; the queue step needs the database, neither step needs torch or a checkpoint):
    python -m evaluation.laya_export queue --dsn $POSTGRES_DSN --source live \\
        --start 2026-09-01 --end 2026-09-30 --out ../data/laya/review_queue.jsonl
    python -m evaluation.laya_export build --queue ../data/laya/review_queue.jsonl \\
        --labels ../data/laya/labels.jsonl --out-dir ../data/laya
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from features.summary import NORMAL_LABEL, PATTERN_LABELS, QUESTION_ID, pattern_questions, summarize_rows, window_to_rows

DEFAULT_SAMPLE_PERMILLE = 5
DEFAULT_MAX_ROWS = 2000
MIN_LABELED_PER_CLASS_WARNING = 50
SPLITS = ("train", "holdout")


def _hash_int(*parts: object, seed: int) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in (seed, *parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def window_id(window: Any) -> str:
    """Stable id for a real window: vessel plus the time of its first report."""
    return f"{window.mmsi}-{window.window_start.isoformat()}"


def keep_window(window: Any, seed: int, sample_permille: int) -> bool:
    """Order-independent thinning, so streaming in MMSI order does not bias which vessels we see."""
    return _hash_int("keep", window.mmsi, window.window_start.isoformat(), seed=seed) % 1000 < sample_permille


def queue_row(window: Any, split: str, source: str) -> dict[str, Any]:
    """One review-queue row: the summary text and where it came from, with no answer."""
    if split not in SPLITS:
        raise ValueError(f"unknown split: {split!r}")
    return {
        "id": window_id(window),
        "split": split,
        "mmsi": window.mmsi,
        "source": source,
        "window_start": window.window_start.isoformat(),
        "window_end": window.window_end.isoformat(),
        "state": json.dumps(summarize_rows(window_to_rows(window))),
        "questions": json.dumps(pattern_questions()),
    }


def collect_queue(
    windows: Iterable[Any], seed: int, sample_permille: int, is_holdout_vessel: Callable[[int], bool],
    source: str, max_rows: int,
) -> list[dict[str, Any]]:
    """Synchronous core: thin real windows into a review queue. Pure, so it is unit-tested."""
    rows: list[dict[str, Any]] = []
    for window in windows:
        if len(rows) >= max_rows:
            break
        if keep_window(window, seed, sample_permille):
            rows.append(queue_row(window, "holdout" if is_holdout_vessel(window.mmsi) else "train", source))
    return rows


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path}:{number}: not valid JSON ({error})") from error
    return rows


def load_labels(path: Path) -> dict[str, str]:
    """`id -> label`, validated. A repeated id must keep the same label."""
    labels: dict[str, str] = {}
    for row in load_jsonl(path):
        if "id" not in row or "label" not in row:
            raise ValueError(f"label row needs 'id' and 'label': {row!r}")
        if row["label"] not in PATTERN_LABELS:
            raise ValueError(f"unknown label {row['label']!r} for {row['id']}; expected one of {list(PATTERN_LABELS)}")
        if labels.get(row["id"], row["label"]) != row["label"]:
            raise ValueError(f"conflicting labels for {row['id']}")
        labels[row["id"]] = row["label"]
    return labels


def training_row(queue_item: dict[str, Any], label: str) -> dict[str, Any]:
    """A queue row plus its human label, in exactly the eight-column schema Laya's notebook loads."""
    gold = {QUESTION_ID: {
        "label": label,
        "probabilities": {key: 1.0 if key == label else 0.0 for key in PATTERN_LABELS},
    }}
    return {
        "id": queue_item["id"],
        "split": queue_item["split"],
        "mmsi": queue_item["mmsi"],
        "pattern": None if label == NORMAL_LABEL else label,
        "severity": None,  # no injection, so there is no severity
        "state": queue_item["state"],
        "questions": queue_item["questions"],
        "gold": json.dumps(gold),
    }


def build_dataset(queue: list[dict[str, Any]], labels: dict[str, str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Join queue and labels. Returns rows per split and a manifest of what was and was not used."""
    rows: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    counts: dict[str, Counter[str]] = {split: Counter() for split in SPLITS}
    queue_ids = {item["id"] for item in queue}
    unlabeled = 0
    for item in queue:
        label = labels.get(item["id"])
        if label is None:
            unlabeled += 1
            continue
        rows[item["split"]].append(training_row(item, label))
        counts[item["split"]][label] += 1
    vessels = {split: {row["mmsi"] for row in rows[split]} for split in SPLITS}
    overlap = vessels["train"] & vessels["holdout"]
    if overlap:
        raise ValueError(f"vessels appear in both splits: {sorted(overlap)[:5]}")
    thin = sorted({
        f"{split}:{label}" for split in SPLITS for label in PATTERN_LABELS
        if counts[split][label] < MIN_LABELED_PER_CLASS_WARNING
    })
    manifest = {
        "labels": list(PATTERN_LABELS),
        "label_source": "human review of real windows; no synthetic injection",
        "queue_rows": len(queue),
        "unlabeled_queue_rows": unlabeled,
        "label_ids_not_in_queue": sorted(set(labels) - queue_ids),
        "counts": {split: dict(counts[split]) for split in SPLITS},
        "sources": sorted({item.get("source", "unknown") for item in queue}),
        "classes_below_minimum": {"minimum": MIN_LABELED_PER_CLASS_WARNING, "split_and_class": thin},
    }
    return rows, manifest


def write_dataset(rows: dict[str, list[dict[str, Any]]], manifest: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for split in SPLITS:
        with (out_dir / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows[split]:
                handle.write(json.dumps(row) + "\n")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


async def _queue(args: argparse.Namespace) -> int:
    from features.pipeline import stream_feature_windows
    from training.dataset_cache import is_validation_vessel

    rows: list[dict[str, Any]] = []
    batch: list[Any] = []

    def drain() -> None:
        room = args.max_rows - len(rows)
        if room > 0:
            rows.extend(collect_queue(batch, args.seed, args.sample_permille, is_validation_vessel, args.source, room))
        batch.clear()

    async for window in stream_feature_windows(
        args.dsn, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end), args.source,
        max_windows=args.max_windows,
    ):
        batch.append(window)
        if len(batch) >= 2000:  # bounded: one batch is thinned, then dropped
            drain()
            if len(rows) >= args.max_rows:
                break
    drain()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    print(f"wrote {len(rows)} queue rows to {out}; label them, then run `build`")
    return 0


def _build(args: argparse.Namespace) -> int:
    rows, manifest = build_dataset(load_jsonl(Path(args.queue)), load_labels(Path(args.labels)))
    write_dataset(rows, manifest, Path(args.out_dir))
    print(json.dumps(manifest, indent=2))
    if manifest["classes_below_minimum"]["split_and_class"]:
        print("some classes are below the minimum label count above; label more windows before fine-tuning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    queue = commands.add_parser("queue", help="sample real windows into a review queue")
    queue.add_argument("--dsn", required=True)
    queue.add_argument("--source", choices=("live", "historical"), default="live")
    queue.add_argument("--start", required=True, help="ISO date/datetime")
    queue.add_argument("--end", required=True, help="ISO date/datetime")
    queue.add_argument("--out", required=True, help="review_queue.jsonl path")
    queue.add_argument("--sample-permille", type=int, default=DEFAULT_SAMPLE_PERMILLE, help="Keep this many windows per 1000.")
    queue.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    queue.add_argument("--max-windows", type=int, help="Development cap on windows read from the database.")
    queue.add_argument("--seed", type=int, default=0)

    build = commands.add_parser("build", help="join a review queue with human labels into Laya jsonl")
    build.add_argument("--queue", required=True)
    build.add_argument("--labels", required=True)
    build.add_argument("--out-dir", required=True)

    args = parser.parse_args(argv)
    return asyncio.run(_queue(args)) if args.command == "queue" else _build(args)


if __name__ == "__main__":
    raise SystemExit(main())
