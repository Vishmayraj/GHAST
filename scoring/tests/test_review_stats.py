import pytest

from review_stats import (
    HYPOTHESIS_CONFIRMED_BY, MIN_REVIEWED_TO_TRUST, REVIEWED_QUERY, compute_review_stats, fetch_reviewed,
    format_review_stats,
)


def row(hypothesis: str, votes: str, verdict: str | None) -> dict:
    return {"hypothesis": hypothesis, "anomaly_type": votes, "review_verdict": verdict}


def test_no_reviewed_incidents_prints_exactly_that() -> None:
    assert format_review_stats(compute_review_stats([])) == "no reviewed incidents"
    # an unreviewed incident is not a reviewed one
    assert format_review_stats(compute_review_stats([row("benign", "prediction_error", None)])) == "no reviewed incidents"


def test_precision_per_hypothesis_uses_the_verdict_mapping() -> None:
    rows = [
        row("targeted_spoof", "prediction_error", "confirmed_spoof"),
        row("targeted_spoof", "prediction_error", "benign"),
        row("freeze_replay", "freeze_replay", "confirmed_spoof"),
        row("equipment_fault", "speed_jump", "equipment_fault"),
        row("jamming", "prediction_error", "benign"),
    ]
    by_hypothesis = {group.key: group for group in compute_review_stats(rows).by_hypothesis}
    assert (by_hypothesis["targeted_spoof"].reviewed, by_hypothesis["targeted_spoof"].correct) == (2, 1)
    assert by_hypothesis["targeted_spoof"].precision == 0.5
    assert by_hypothesis["freeze_replay"].precision == 1.0
    assert by_hypothesis["equipment_fault"].precision == 1.0
    assert by_hypothesis["jamming"].precision == 0.0


def test_unclear_verdicts_are_counted_but_leave_the_denominator() -> None:
    rows = [
        row("targeted_spoof", "prediction_error", "confirmed_spoof"),
        row("targeted_spoof", "prediction_error", "unclear"),
    ]
    stats = compute_review_stats(rows)
    group = stats.by_hypothesis[0]
    assert (stats.reviewed, stats.unclear) == (2, 1)
    assert (group.reviewed, group.unclear, group.decided, group.precision) == (2, 1, 1, 1.0)


def test_a_group_with_only_unclear_verdicts_has_no_precision() -> None:
    group = compute_review_stats([row("benign", "prediction_error", "unclear")]).by_hypothesis[0]
    assert group.precision is None
    assert " n/a" in format_review_stats(compute_review_stats([row("benign", "prediction_error", "unclear")]))


def test_precision_per_vote_combination_counts_any_real_event() -> None:
    rows = [
        row("targeted_spoof", "freeze_replay+prediction_error", "confirmed_spoof"),
        row("equipment_fault", "freeze_replay+prediction_error", "equipment_fault"),
        row("benign", "freeze_replay+prediction_error", "benign"),
        row("targeted_spoof", "prediction_error", "benign"),
        row("unresolved", "prediction_error", "jamming"),
    ]
    by_votes = {group.key: group for group in compute_review_stats(rows).by_votes}
    assert by_votes["freeze_replay+prediction_error"].precision == pytest.approx(2 / 3)
    assert by_votes["prediction_error"].precision == 0.5


def test_unresolved_hypothesis_is_never_a_hit() -> None:
    assert "unresolved" not in HYPOTHESIS_CONFIRMED_BY
    group = compute_review_stats([row("unresolved", "prediction_error", "jamming")]).by_hypothesis[0]
    assert group.correct == 0


def test_under_thirty_reviewed_says_too_few_to_trust() -> None:
    text = format_review_stats(compute_review_stats([row("benign", "prediction_error", "benign")]))
    assert "too few to trust" in text and f"fewer than {MIN_REVIEWED_TO_TRUST}" in text


def test_thirty_reviewed_drops_the_overall_note_but_small_rows_stay_marked() -> None:
    rows = [row("targeted_spoof", "prediction_error", "confirmed_spoof")] * MIN_REVIEWED_TO_TRUST
    rows.append(row("benign", "speed_jump", "benign"))
    text = format_review_stats(compute_review_stats(rows))
    assert "too few to trust" not in text
    benign_line = next(line for line in text.splitlines() if line.startswith("benign"))
    targeted_line = next(line for line in text.splitlines() if line.startswith("targeted_spoof"))
    assert benign_line.endswith("*") and not targeted_line.endswith("*")


class FakeDb:
    def __init__(self, rows: list[dict]) -> None:
        self.rows, self.queries = rows, []

    async def fetch(self, query, *args):
        self.queries.append(query)
        return self.rows


@pytest.mark.asyncio
async def test_only_reviewed_incidents_are_read() -> None:
    db = FakeDb([row("benign", "prediction_error", "benign")])
    assert await fetch_reviewed(db) == db.rows
    assert db.queries == [REVIEWED_QUERY]
    assert "review_verdict IS NOT NULL" in REVIEWED_QUERY
