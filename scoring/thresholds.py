"""Look at, retune, or set the alert thresholds.

    cd scoring
    python thresholds.py show                       # active A and B, how many reports back them, flag shares
    python thresholds.py history [--limit 10]       # every change, who made it, and why
    python thresholds.py retune [--dry-run]         # ask the threshold agent now (needs the checkpoint for the model version)
    python thresholds.py set --a 0.2 --b 1.0 [--reason "..."]   # analyst override, recorded as set_by=manual

A manual `set` is a normal row in `threshold_config`, so the agent sees it as the active value
and may move it later (bounded, see agent/threshold_agent/agent.py). To pin a value, stop the
agent's retune with `live_scorer.py --retune-interval-seconds` set very high.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _package_dir in ("ml", "agent"):
    _path = str(_REPO_ROOT / _package_dir)
    if _path not in sys.path:
        sys.path.append(_path)

from features.score_histogram import share_above  # noqa: E402
from threshold_agent.agent import (  # noqa: E402
    ABSOLUTE_MAX, ABSOLUTE_MIN, MIN_B_OVER_A, INSERT_QUERY, Budgets, _merged_histogram, build_threshold_agent, get_active, retune,
)

HISTORY_QUERY = "SELECT created_at, threshold_a, threshold_b, model_version, set_by, reason FROM threshold_config ORDER BY id DESC LIMIT $1"


def check_pair(a: float, b: float) -> None:
    """Raise ValueError unless (a, b) is a sane threshold pair."""
    if not ABSOLUTE_MIN <= a <= ABSOLUTE_MAX:
        raise ValueError(f"A must be between {ABSOLUTE_MIN} and {ABSOLUTE_MAX} degrees")
    if b < a * MIN_B_OVER_A:
        raise ValueError(f"B must be at least {MIN_B_OVER_A}x A")


async def show(db: Any) -> str:
    active = await get_active(db)
    if active is None:
        return "no thresholds set: the scorer is only observing. Run `retune` once it has recorded stats, or `set --a .. --b ..`."
    version = active["model_version"]
    counts, scored, skipped = await _merged_histogram(db, version)
    lines = [f"A={active['threshold_a']:.6g}  B={active['threshold_b']:.6g}  set by {active['set_by']} at {active['created_at']:%Y-%m-%d %H:%M}",
             f"model: {version}", f"reason: {active['reason']}"]
    if counts:
        lines.append(f"last 72h on this model: {scored} reports scored, {skipped} skipped as unscorable")
        for name, key in (("A", "threshold_a"), ("B", "threshold_b")):
            share = share_above(counts, float(active[key]))
            lines.append(f"  flag share at {name}: {share:.2e}  (about {share * scored:.1f} flags in the window, before debounce)")
    else:
        lines.append("no scoring stats for this model version yet")
    return "\n".join(lines)


async def history(db: Any, limit: int) -> str:
    rows = await db.fetch(HISTORY_QUERY, limit)
    if not rows:
        return "no threshold changes recorded"
    return "\n".join(f"{r['created_at']:%Y-%m-%d %H:%M}  A={r['threshold_a']:.6g} B={r['threshold_b']:.6g}  {r['set_by']:<7} {r['model_version'] or '-'}  {r['reason'] or ''}" for r in rows)


async def set_manual(db: Any, a: float, b: float, model_version: str | None, reason: str | None) -> None:
    check_pair(a, b)
    await db.fetchval(INSERT_QUERY, a, b, model_version, "manual", (reason or "set by hand")[:500])


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    pool = await asyncpg.create_pool(args.dsn, min_size=1, max_size=2)
    try:
        if args.command == "show":
            print(await show(pool))
        elif args.command == "history":
            print(await history(pool, args.limit))
        elif args.command == "set":
            active = await get_active(pool)
            await set_manual(pool, args.a, args.b, active["model_version"] if active else args.model_version, args.reason)
            print(f"set A={args.a} B={args.b} (manual)")
        elif args.command == "retune":
            from dotenv import load_dotenv

            load_dotenv(_REPO_ROOT / ".env")
            active = await get_active(pool)
            version = args.model_version or (active["model_version"] if active else None)
            client = None
            if os.environ.get("GROQ_API_KEY"):
                from groq import AsyncGroq
                client = AsyncGroq()
            model = os.environ.get("GHAST_AGENT_MODEL") or os.environ.get("GHAST_REPORT_MODEL") or "openai/gpt-oss-120b"
            budgets = Budgets(args.budget_a, args.budget_b) if args.budget_a else Budgets()
            agent = build_threshold_agent(pool, client, model, version, budgets, _REPO_ROOT / "ml" / "reports")
            print(await retune(pool, agent, version, budgets, dry_run=args.dry_run))
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        await pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=os.environ.get("POSTGRES_DSN"), help="Defaults to $POSTGRES_DSN.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show")
    history_parser = commands.add_parser("history")
    history_parser.add_argument("--limit", type=int, default=10)
    set_parser = commands.add_parser("set")
    set_parser.add_argument("--a", type=float, required=True)
    set_parser.add_argument("--b", type=float, required=True)
    set_parser.add_argument("--reason")
    set_parser.add_argument("--model-version", help="Only used when no thresholds exist yet.")
    retune_parser = commands.add_parser("retune")
    retune_parser.add_argument("--dry-run", action="store_true")
    retune_parser.add_argument("--model-version")
    retune_parser.add_argument("--budget-a", type=float)
    retune_parser.add_argument("--budget-b", type=float, default=None)
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("--dsn is required (or set POSTGRES_DSN)")
    if args.command == "set":
        try:
            check_pair(args.a, args.b)
        except ValueError as error:
            parser.error(str(error))
    if args.command == "retune" and args.budget_a and not args.budget_b:
        parser.error("--budget-b is required with --budget-a")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
