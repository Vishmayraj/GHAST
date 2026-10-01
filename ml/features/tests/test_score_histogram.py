import numpy as np
import pytest

from features.score_histogram import (
    HISTOGRAM_EDGES, N_BINS, histogram_counts, merge_counts, share_above, threshold_for_share,
)


def test_every_finite_value_lands_in_a_bin_including_extremes():
    counts = histogram_counts([0.0, 1e-9, 0.5, 1e3, np.nan, np.inf])
    assert len(counts) == N_BINS and sum(counts) == 4
    assert counts[0] == 2 and counts[-1] == 1


def test_share_above_matches_the_exact_rate_closely():
    rng = np.random.default_rng(0)
    errors = 10 ** rng.normal(-2.5, 0.6, 50_000)
    counts = histogram_counts(errors)
    for threshold in (0.001, 0.005, 0.02, 0.1):
        assert share_above(counts, threshold) == pytest.approx(float(np.mean(errors > threshold)), abs=0.01)


def test_threshold_for_share_inverts_share_above():
    rng = np.random.default_rng(1)
    counts = histogram_counts(10 ** rng.normal(-2.0, 0.7, 80_000))
    for share in (0.05, 0.01, 0.001):
        cut = threshold_for_share(counts, share)
        assert share_above(counts, cut) == pytest.approx(share, rel=0.15)


def test_empty_histograms_are_neutral():
    empty = histogram_counts([])
    assert share_above(empty, 0.1) == 0.0 and threshold_for_share(empty, 0.01) is None


def test_edges_and_bounds():
    assert share_above(histogram_counts([0.1]), HISTOGRAM_EDGES[0] / 2) == 1.0
    assert share_above(histogram_counts([0.1]), HISTOGRAM_EDGES[-1] * 2) == 0.0
    with pytest.raises(ValueError):
        threshold_for_share(histogram_counts([0.1]), 1.5)


def test_merge_counts_adds_and_rejects_wrong_length():
    a, b = histogram_counts([0.01]), histogram_counts([0.01, 0.5])
    assert sum(merge_counts([a, b])) == 3
    with pytest.raises(ValueError):
        merge_counts([[1, 2]])
