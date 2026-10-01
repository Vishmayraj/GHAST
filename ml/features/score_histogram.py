"""Fixed log-spaced histograms of prediction error, so a flag rate at any threshold can be
recomputed later from stored per-cycle counts instead of one row per report.

Pure numpy, no torch: the live scorer writes these, the threshold agent reads them.
Errors are in degrees. Bin i holds errors in [EDGES[i], EDGES[i+1]); the first bin also takes
everything below EDGES[0] and the last bin everything at or above EDGES[-1], so no report is
ever dropped. Rates computed at a threshold that falls inside a bin are linearly interpolated
within that bin, which is exact to within one bin's width (about 12 percent in log terms).
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

LOW_EXP, HIGH_EXP, BINS_PER_DECADE = -5, 1, 20  # 1e-5 .. 1e1 degrees
HISTOGRAM_EDGES = np.logspace(LOW_EXP, HIGH_EXP, (HIGH_EXP - LOW_EXP) * BINS_PER_DECADE + 1)
N_BINS = len(HISTOGRAM_EDGES) - 1


def histogram_counts(errors: np.ndarray | Sequence[float]) -> list[int]:
    """Counts per bin, length N_BINS. Non-finite values are ignored."""
    values = np.asarray(errors, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return [0] * N_BINS
    clipped = np.clip(values, HISTOGRAM_EDGES[0], np.nextafter(HISTOGRAM_EDGES[-1], 0))
    counts, _ = np.histogram(clipped, bins=HISTOGRAM_EDGES)
    return [int(c) for c in counts]


def merge_counts(histograms: Sequence[Sequence[int]]) -> list[int]:
    total = np.zeros(N_BINS, dtype=np.int64)
    for histogram in histograms:
        if len(histogram) != N_BINS:
            raise ValueError(f"histogram has {len(histogram)} bins, expected {N_BINS}")
        total += np.asarray(histogram, dtype=np.int64)
    return [int(c) for c in total]


def share_above(counts: Sequence[int], threshold: float) -> float:
    """Approximate share of reports with error strictly above `threshold` (0 when empty)."""
    arr = np.asarray(counts, dtype=np.float64)
    total = arr.sum()
    if total == 0:
        return 0.0
    if threshold <= HISTOGRAM_EDGES[0]:
        return 1.0
    if threshold >= HISTOGRAM_EDGES[-1]:
        return 0.0
    index = int(np.searchsorted(HISTOGRAM_EDGES, threshold, side="right") - 1)
    low, high = HISTOGRAM_EDGES[index], HISTOGRAM_EDGES[index + 1]
    inside = (np.log(high) - np.log(threshold)) / (np.log(high) - np.log(low))
    above = arr[index + 1:].sum() + arr[index] * inside
    return float(above / total)


def threshold_for_share(counts: Sequence[int], target_share: float) -> float | None:
    """Smallest threshold whose flag share is at most `target_share`; None when empty."""
    if not 0.0 < target_share < 1.0:
        raise ValueError("target_share must be between 0 and 1 (exclusive)")
    arr = np.asarray(counts, dtype=np.float64)
    total = arr.sum()
    if total == 0:
        return None
    allowed_above = target_share * total
    cumulative_from_top = np.cumsum(arr[::-1])[::-1]  # reports in this bin or higher
    for index in range(N_BINS - 1, -1, -1):
        if cumulative_from_top[index] > allowed_above:
            above_bin = cumulative_from_top[index] - arr[index]
            need_from_bin = allowed_above - above_bin  # reports allowed above the cut inside this bin
            fraction_kept = need_from_bin / arr[index] if arr[index] else 0.0
            low, high = HISTOGRAM_EDGES[index], HISTOGRAM_EDGES[index + 1]
            return float(np.exp(np.log(high) - fraction_kept * (np.log(high) - np.log(low))))
    return float(HISTOGRAM_EDGES[0])
