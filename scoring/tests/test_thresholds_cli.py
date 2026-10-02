import json
from datetime import datetime, timezone

import numpy as np
import pytest

from features.score_histogram import histogram_counts
import thresholds as cli


class Db:
    def __init__(self, active=None, stats=(), history=()):
        self.active, self.stats, self.hist, self.inserted = active, list(stats), list(history), []

    async def fetchrow(self, q, *a):
        return self.active

    async def fetch(self, q, *a):
        return self.hist if "ORDER BY id DESC LIMIT" in q else self.stats

    async def fetchval(self, q, *a):
        self.inserted.append(a)


def active(a=0.2, b=1.0):
    return {"threshold_a": a, "threshold_b": b, "model_version": "v2", "set_by": "agent", "reason": "budget", "created_at": datetime(2026, 10, 2, tzinfo=timezone.utc)}


@pytest.mark.parametrize("a,b,ok", [(0.2, 1.0, True), (0.2, 0.25, False), (1e-9, 1.0, False), (50.0, 100.0, False)])
def test_check_pair(a, b, ok):
    if ok:
        cli.check_pair(a, b)
    else:
        with pytest.raises(ValueError):
            cli.check_pair(a, b)


@pytest.mark.asyncio
async def test_show_says_the_scorer_is_observing_when_nothing_is_set():
    assert "only observing" in await cli.show(Db())


@pytest.mark.asyncio
async def test_show_reports_flag_shares_from_recorded_stats():
    errors = 10 ** np.random.default_rng(0).normal(-2.3, 0.5, 50_000)
    stats = [{"reports_scored": 50_000, "skipped_unscorable": 12, "flagged_a": 0, "flagged_b": 0, "histogram": json.dumps(histogram_counts(errors))}]
    text = await cli.show(Db(active(), stats))
    assert "A=0.2" in text and "50000 reports scored, 12 skipped" in text and "flag share at A" in text and "flag share at B" in text


@pytest.mark.asyncio
async def test_set_manual_records_the_override_and_refuses_bad_pairs():
    db = Db()
    await cli.set_manual(db, 0.2, 1.0, "v2", None)
    assert db.inserted[0][:4] == (0.2, 1.0, "v2", "manual")
    with pytest.raises(ValueError):
        await cli.set_manual(db, 0.2, 0.21, "v2", None)
    assert len(db.inserted) == 1


@pytest.mark.asyncio
async def test_history_lists_newest_first_with_who_and_why():
    row = {"created_at": datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc), "threshold_a": 0.2, "threshold_b": 1.0, "model_version": "v2", "set_by": "manual", "reason": "hand"}
    text = await cli.history(Db(history=[row]), 5)
    assert "manual" in text and "hand" in text and "2026-10-02 09:30" in text
    assert await cli.history(Db(), 5) == "no threshold changes recorded"


def test_set_with_a_bad_pair_is_a_usage_error():
    with pytest.raises(SystemExit):
        cli.main(["--dsn", "postgresql://x", "set", "--a", "0.2", "--b", "0.1"])
