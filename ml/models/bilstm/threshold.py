"""The chosen operating threshold for `ml/models/bilstm/infer.py::prediction_errors()`.

`scoring/live_scorer.py` (the live scoring service) imports OPERATING_THRESHOLD from
here rather than each caller picking its own number. This module holds exactly one
documented constant plus the provenance comment that justifies it - see
`ImplementationPlans/Sem5_Evaluation_And_Threshold.md`,
`ImplementationPlans/Sem5_Evaluation_Followup.md`, and
`ImplementationPlans/Sem5_BigPass_LiveScoring_And_Laya.md` (in that order) for how it
was produced.

Provenance (fill in and update this block every time OPERATING_THRESHOLD changes):
    checkpoint:            checkpoints/epoch_010.pt (epoch 10, train_loss=0.0006055,
                           val_loss=0.0007673)
    holdout method:        validation_vessel_split (fraction=0.2), via
                           training.dataset_cache.is_validation_vessel - see
                           ImplementationPlans/Sem5_Evaluation_Followup.md section 4
    eval scale:            ~6.4M scored observations (control + the four
                           features.inject.SPOOF_PATTERNS), full corrected run, not the
                           1,000-window development cap
    chosen tradeoff:       max F1
    f1 at this threshold:  0.424 (precision/recall weren't separately recorded in the
                           handoff summary this number was carried forward from - see
                           ImplementationPlans/Sem5_BigPass_LiveScoring_And_Laya.md
                           section 1; pull them from the MLflow run below if needed)
    beats speed_jump baseline?: yes (0.424 vs 0.224)
    MLflow experiment:     bilstm-checkpoint-scoring (run ID not recorded in the
                           handoff summary - look it up via `mlflow ui
                           --backend-store-uri sqlite:///mlruns/mlflow.db` if needed)

    Note on this entry's provenance: the run itself was executed elsewhere (this
    number was handed off via ImplementationPlans/Sem5_BigPass_LiveScoring_And_Laya.md
    rather than produced by re-running evaluation/score_checkpoint.py in this session,
    which had no database or GPU access) - see that document for the full context. If
    that changes and a new run supersedes this one, update every field above, not just
    the number, so this block never drifts out of sync with what's actually deployed.

OPERATING_THRESHOLD was None until evaluation/score_checkpoint.py was run at real
scale against a real checkpoint and injected_synthetic eval range - see that script's
`--help` for usage, and ImplementationPlans/Sem5_Evaluation_Followup.md for why a
guessed number here would have been worse than refusing to score at all. A live
scoring service should still treat `OPERATING_THRESHOLD is None` as "not ready to
score" rather than falling back to a guessed default - that branch stays load-bearing
even now that a real value is set, in case this ever needs resetting to None again
(e.g. a retrained checkpoint whose own threshold hasn't been swept yet).
"""
from __future__ import annotations

OPERATING_THRESHOLD: float | None = 0.004946
