"""Analyst review CLI for incidents the agent has stored.

    cd agent
    python review.py list [--limit 25]
    python review.py show <incident-id>
    python review.py verdict <incident-id> <verdict> [--notes "..."] [--reviewer NAME] [--force]

`list` shows unreviewed incidents, newest first. `show` prints the stored evidence and the
20-report summary text the Laya classifier reads. `verdict` records the analyst's call and
sets status = 'resolved', which is what lifts the live scorer's debounce for that vessel
(scoring/live_scorer.py only skips vessels with an incident that is not resolved).

Only incidents that already exist are reviewed. Nothing in this module creates one.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

# The repo's packages use flat imports (see agent/pytest.ini); make `python review.py`
# work from a checkout the same way scoring/live_scorer.py does.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _package_dir in ("ml", "agent"):
    _path = str(_REPO_ROOT / _package_dir)
    if _path not in sys.path:
        sys.path.append(_path)

from features.summary import summarize_rows  # noqa: E402
from tools.pattern_classifier import WINDOW_LENGTH, recent_window  # noqa: E402

VERDICTS = ("confirmed_spoof", "jamming", "equipment_fault", "benign", "unclear")

LIST_QUERY = """
SELECT id, mmsi, flagged_at, window_start, window_end, hypothesis, confidence, status,
       anomaly_type, (report_text IS NOT NULL) AS has_report
FROM incidents
WHERE review_verdict IS NULL
ORDER BY flagged_at DESC
LIMIT $1
"""

SHOW_QUERY = """
SELECT id, mmsi, flagged_at, window_start, window_end, hypothesis, confidence, status,
       anomaly_type, anomaly_score, evidence, report_text,
       review_verdict, reviewed_by, reviewed_at, review_notes
FROM incidents
WHERE id = $1::uuid
"""

# The WHERE guard means a verdict is never overwritten unless the caller passed force.
VERDICT_QUERY = """
UPDATE incidents
SET review_verdict = $2, reviewed_by = $3, reviewed_at = now(), review_notes = $4,
    status = 'resolved', updated_at = now()
WHERE id = $1::uuid AND (review_verdict IS NULL OR $5::boolean)
RETURNING id
"""


async def list_unreviewed(db: Any, limit: int = 25) -> list[dict[str, Any]]:
    return [dict(row) for row in await db.fetch(LIST_QUERY, limit)]


async def get_incident(db: Any, incident_id: str) -> dict[str, Any] | None:
    row = await db.fetchrow(SHOW_QUERY, incident_id)
    return dict(row) if row is not None else None


async def record_verdict(
    db: Any, incident_id: str, verdict: str, reviewer: str, notes: str | None = None, force: bool = False,
) -> str:
    """Store a verdict. Returns "recorded", "not_found" or "already_reviewed"."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    existing = await get_incident(db, incident_id)
    if existing is None:
        return "not_found"
    if existing.get("review_verdict") is not None and not force:
        return "already_reviewed"
    row = await db.fetchrow(VERDICT_QUERY, incident_id, verdict, reviewer, notes, force)
    return "recorded" if row is not None else "already_reviewed"


def _as_json(value: Any) -> Any:
    """asyncpg returns jsonb as text unless a codec is registered; accept either."""
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _stamp(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else "n/a"


def format_list(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return "no unreviewed incidents"
    blocks = []
    for row in rows:
        confidence = row.get("confidence")
        confidence_text = "n/a" if confidence is None else f"{confidence:.2f}"
        report = f"stored (python review.py show {row['id']})" if row.get("has_report") else "none"
        blocks.append(
            f"{row['id']}  flagged {_stamp(row['flagged_at'])}  mmsi={row['mmsi']}\n"
            f"  hypothesis={row['hypothesis']} confidence={confidence_text} status={row['status']}\n"
            f"  votes={row.get('anomaly_type') or 'n/a'}  span={_stamp(row.get('window_start'))} to {_stamp(row.get('window_end'))}\n"
            f"  report: {report}"
        )
    return "\n\n".join(blocks)


def window_summary(evidence: dict[str, Any], flagged_at: Any) -> str:
    """The text the Laya classifier saw, rebuilt from the stored track_history evidence."""
    positions = (evidence.get("track_history") or {}).get("positions") or []
    rows = []
    for position in positions:
        received_at = position.get("received_at")
        if isinstance(received_at, str):
            received_at = datetime.fromisoformat(received_at)
        rows.append({**position, "received_at": received_at})
    window = recent_window(rows, flagged_at)
    if len(window) < WINDOW_LENGTH:
        return f"unavailable: only {len(window)} stored reports up to the flag, need {WINDOW_LENGTH}"
    return summarize_rows(window)


def _compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Collapse the raw position list (hundreds of rows) to a count and its ends."""
    compact = dict(evidence)
    track = evidence.get("track_history")
    if isinstance(track, dict) and isinstance(track.get("positions"), list):
        positions = track["positions"]
        compact["track_history"] = {
            **{key: value for key, value in track.items() if key != "positions"},
            "position_count": len(positions),
            "first_position": positions[0] if positions else None,
            "last_position": positions[-1] if positions else None,
        }
    return compact


def format_show(row: dict[str, Any]) -> str:
    evidence = _as_json(row.get("evidence")) or {}
    lines = [
        f"incident {row['id']}",
        f"mmsi={row['mmsi']}  flagged {_stamp(row['flagged_at'])}  score={row['anomaly_score']:.6f}  votes={row.get('anomaly_type') or 'n/a'}",
        f"hypothesis={row['hypothesis']}  confidence={row.get('confidence')}  status={row['status']}",
        f"span={_stamp(row.get('window_start'))} to {_stamp(row.get('window_end'))}",
    ]
    if row.get("review_verdict") is not None:
        lines.append(f"reviewed: {row['review_verdict']} by {row.get('reviewed_by')} at {_stamp(row.get('reviewed_at'))}  notes={row.get('review_notes')!r}")
    lines += [
        "",
        "evidence (raw position list collapsed to its ends; tool_call_log omitted, same content):",
        json.dumps(_compact_evidence(evidence), indent=2, default=str),
        "",
        "20-report summary (the text the Laya classifier reads):",
        window_summary(evidence, row["flagged_at"]),
        "",
        "stored report:",
        row.get("report_text") or "none",
    ]
    return "\n".join(lines)


def _valid_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a full incident id (UUID)") from error


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    connection = await asyncpg.connect(args.dsn)
    try:
        if args.command == "list":
            print(format_list(await list_unreviewed(connection, args.limit)))
            return 0
        if args.command == "show":
            row = await get_incident(connection, args.incident_id)
            if row is None:
                print(f"no incident {args.incident_id}", file=sys.stderr)
                return 1
            print(format_show(row))
            return 0
        outcome = await record_verdict(connection, args.incident_id, args.verdict, args.reviewer, args.notes, args.force)
        if outcome == "not_found":
            print(f"no incident {args.incident_id}", file=sys.stderr)
            return 1
        if outcome == "already_reviewed":
            print("incident already has a verdict; pass --force to replace it", file=sys.stderr)
            return 1
        print(f"recorded {args.verdict} for {args.incident_id}; status is now resolved")
        return 0
    finally:
        await connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=os.environ.get("POSTGRES_DSN"), help="Defaults to $POSTGRES_DSN.")
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list", help="Unreviewed incidents, newest first.")
    list_parser.add_argument("--limit", type=int, default=25)
    show_parser = commands.add_parser("show", help="Evidence and the 20-report summary for one incident.")
    show_parser.add_argument("incident_id", type=_valid_id)
    verdict_parser = commands.add_parser("verdict", help="Record the analyst verdict and resolve the incident.")
    verdict_parser.add_argument("incident_id", type=_valid_id)
    verdict_parser.add_argument("verdict", choices=VERDICTS)
    verdict_parser.add_argument("--notes")
    verdict_parser.add_argument("--reviewer", default=getpass.getuser(), help="Defaults to the OS user.")
    verdict_parser.add_argument("--force", action="store_true", help="Replace an existing verdict.")
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("--dsn is required (or set POSTGRES_DSN)")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
