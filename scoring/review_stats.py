"""Precision numbers from analyst-reviewed incidents.

    cd scoring
    python review_stats.py --dsn postgresql://ghast:ghast@localhost:5432/ghast

Reads incidents that have a review_verdict (recorded with agent/review.py) and prints two
tables:

* precision per agent hypothesis: how often the agent's hypothesis matches the analyst's
  verdict (HYPOTHESIS_CONFIRMED_BY below),
* precision per detector-vote combination: how often a flag with those votes was a real
  event at all (REAL_EVENT_VERDICTS below), whatever the agent concluded.

"unclear" verdicts are counted but left out of every precision denominator. Nothing here
creates or alters incidents, and with no reviewed incidents it prints exactly that.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Below this many reviewed incidents the numbers are noise. The total gets a plain note and
# any single row under it gets a marker. A starting value, not a calibrated one.
MIN_REVIEWED_TO_TRUST = 30

UNCLEAR = "unclear"

# Verdicts that mean the agent's hypothesis was right. The agent has no hypothesis for the
# verdict-side names, so this is the one mapping between the two vocabularies.
# `unresolved` is deliberately absent: it is the agent declining to decide, never a hit.
HYPOTHESIS_CONFIRMED_BY: dict[str, frozenset[str]] = {
    "jamming": frozenset({"jamming"}),
    "targeted_spoof": frozenset({"confirmed_spoof"}),
    "freeze_replay": frozenset({"confirmed_spoof"}),
    "equipment_fault": frozenset({"equipment_fault"}),
    "benign": frozenset({"benign"}),
}

# Verdicts meaning the flag pointed at something real. equipment_fault counts as real: the
# detectors did catch a genuine anomaly, it just was not hostile. benign is the false alarm.
REAL_EVENT_VERDICTS = frozenset({"confirmed_spoof", "jamming", "equipment_fault"})

REVIEWED_QUERY = """
SELECT hypothesis, anomaly_type, review_verdict
FROM incidents
WHERE review_verdict IS NOT NULL
"""


@dataclass(frozen=True)
class GroupStats:
    key: str
    reviewed: int
    unclear: int
    correct: int

    @property
    def decided(self) -> int:
        return self.reviewed - self.unclear

    @property
    def precision(self) -> float | None:
        return self.correct / self.decided if self.decided else None


@dataclass(frozen=True)
class ReviewStats:
    reviewed: int
    unclear: int
    by_hypothesis: list[GroupStats]
    by_votes: list[GroupStats]


def _group(rows: Iterable[Mapping[str, Any]], key_of, is_correct) -> list[GroupStats]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for row in rows:
        entry = counts[key_of(row)]
        entry[0] += 1
        if row["review_verdict"] == UNCLEAR:
            entry[1] += 1
        elif is_correct(row):
            entry[2] += 1
    return [GroupStats(key, *entry) for key, entry in sorted(counts.items())]


def compute_review_stats(rows: Iterable[Mapping[str, Any]]) -> ReviewStats:
    reviewed_rows = [row for row in rows if row.get("review_verdict") is not None]
    return ReviewStats(
        reviewed=len(reviewed_rows),
        unclear=sum(1 for row in reviewed_rows if row["review_verdict"] == UNCLEAR),
        by_hypothesis=_group(
            reviewed_rows,
            lambda row: row["hypothesis"],
            lambda row: row["review_verdict"] in HYPOTHESIS_CONFIRMED_BY.get(row["hypothesis"], frozenset()),
        ),
        by_votes=_group(
            reviewed_rows,
            lambda row: row.get("anomaly_type") or "untracked",
            lambda row: row["review_verdict"] in REAL_EVENT_VERDICTS,
        ),
    )


def _table(title: str, key_label: str, groups: Sequence[GroupStats]) -> list[str]:
    width = max([len(key_label)] + [len(group.key) for group in groups])
    lines = [title, f"{key_label:<{width}}  reviewed  unclear  correct  precision"]
    for group in groups:
        precision = "n/a" if group.precision is None else f"{group.precision:.2f}"
        marker = " *" if group.reviewed < MIN_REVIEWED_TO_TRUST else ""
        lines.append(f"{group.key:<{width}}  {group.reviewed:>8}  {group.unclear:>7}  {group.correct:>7}  {precision:>9}{marker}")
    return lines


def format_review_stats(stats: ReviewStats) -> str:
    if stats.reviewed == 0:
        return "no reviewed incidents"
    lines = [f"reviewed incidents: {stats.reviewed} (unclear: {stats.unclear}, excluded from precision)"]
    if stats.reviewed < MIN_REVIEWED_TO_TRUST:
        lines.append(f"too few to trust: fewer than {MIN_REVIEWED_TO_TRUST} reviewed incidents, treat these as anecdotes")
    lines.append("")
    lines += _table("precision per hypothesis (agent hypothesis matches the analyst verdict)", "hypothesis", stats.by_hypothesis)
    lines.append("")
    lines += _table("precision per detector votes (flag was a real event: confirmed_spoof, jamming or equipment_fault)", "votes", stats.by_votes)
    lines.append("")
    lines.append(f"* fewer than {MIN_REVIEWED_TO_TRUST} reviewed in this row")
    return "\n".join(lines)


async def fetch_reviewed(db: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in await db.fetch(REVIEWED_QUERY)]


async def _run(dsn: str) -> int:
    import asyncpg

    connection = await asyncpg.connect(dsn)
    try:
        print(format_review_stats(compute_review_stats(await fetch_reviewed(connection))))
        return 0
    finally:
        await connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=os.environ.get("POSTGRES_DSN"), help="Defaults to $POSTGRES_DSN.")
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("--dsn is required (or set POSTGRES_DSN)")
    return asyncio.run(_run(args.dsn))


if __name__ == "__main__":
    raise SystemExit(main())
