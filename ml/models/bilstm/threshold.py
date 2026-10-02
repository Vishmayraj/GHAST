"""The chosen operating threshold for `ml/models/bilstm/infer.py::prediction_errors()`.

`scoring/live_scorer.py` (the live scoring service) imports OPERATING_THRESHOLD from
here rather than each caller picking its own number. This module holds exactly one
documented constant plus the provenance comment that justifies it.

Provenance (fill in and update this block every time OPERATING_THRESHOLD changes):
    checkpoint:            checkpoints/epoch_010.pt (epoch 10, train_loss=0.0006055,
                           val_loss=0.0007673)
    how it was chosen:     max F1 on labels made by a synthetic spoof injector
                           (four patterns injected into held-out historical windows,
                           validation vessel split, ~6.4M scored reports). That injector
                           and its evaluation path have been removed from the repo. The
                           number is therefore a leftover from a measurement of injected
                           spoofs, not of real spoofing, and its 19.4% control flag rate
                           was measured on the same data it was tuned on.
    f1 at that time:       0.424 (speed_jump baseline 0.224); precision and recall were
                           not recorded
    status:                PLACEHOLDER UNTIL RE-DERIVED. Real-traffic reports now exist
                           (ml/reports/historical_epoch_010.json, live_epoch_010.json) and
                           show this value flags 33.4% (historical) and 39.3% (live) of
                           reports, so it is not an alert budget. No replacement chosen:
                           the live report predates the data-quality gate and its tail is
                           not usable, and the alert budget is an owner decision. See
                           docs/research_notes/threshold-recalibration.md. Live alert
                           thresholds come from threshold_config, not from this constant.
    run provenance:        executed on another machine; no MLflow run id or result file is
                           in the repository, so none of the above can be reproduced here.

When a new value replaces this one, update every field above, not just the number, so
this block never drifts out of sync with what is deployed.

A live scoring service should treat `OPERATING_THRESHOLD is None` as "not ready to score"
rather than falling back to a guessed default. That branch stays load-bearing even now
that a value is set, in case this ever needs resetting (for example a retrained checkpoint
whose own threshold has not been calibrated yet).
"""
from __future__ import annotations

OPERATING_THRESHOLD: float | None = 0.004946
