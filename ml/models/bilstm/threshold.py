"""The chosen operating threshold for `ml/models/bilstm/infer.py::prediction_errors()`.

A later document's live scoring service imports OPERATING_THRESHOLD from here rather
than each caller picking its own number. This module holds exactly one documented
constant plus the provenance comment that justifies it - see
`ImplementationPlans/Sem5_Evaluation_And_Threshold.md` for how it's produced.

Provenance (fill in and update this block every time OPERATING_THRESHOLD changes):
    checkpoint:            <not yet run - e.g. checkpoints/epoch_010.pt>
    checkpoint epoch:      <epoch field from that checkpoint>
    eval range:            <eval_start>..<eval_end> (disjoint from the training range)
    eval seed:             <seed passed to build_synthetic_dataset>
    chosen tradeoff:        <e.g. "max F1" or "min recall 0.9, break ties on precision">
    precision / recall / f1 at this threshold: <from the sweep's printed output>
    beats speed_jump baseline?: <yes/no, with both F1s>
    MLflow run:            <run ID under MLFLOW_TRACKING_URI, if logged>

OPERATING_THRESHOLD is intentionally left as None until `evaluation/score_checkpoint.py`
has actually been run against a real checkpoint and a real injected_synthetic eval
range - see that script's `--help` for usage. A None here is a deliberate signal that
no run has produced a defensible number yet; a live scoring service should treat
`OPERATING_THRESHOLD is None` as "not ready to score" rather than falling back to a
guessed default; picking a plausible-looking number without a real sweep behind it
would be worse than refusing to score at all.
"""
from __future__ import annotations

OPERATING_THRESHOLD: float | None = None
