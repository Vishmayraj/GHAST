# ml/

The detection layer: trajectory-anomaly models that score incoming vessel tracks for spoofing likelihood. Stage 1 scope is the Bi-LSTM model only (see `ImplementationPlans/Sem5IP.md` section 2) — the transformer migration and fleet-wide DBSCAN clustering are Stage 2.

- `features/` — feature extraction pipeline (speed, course, rate of turn, vessel class).
- `models/bilstm/` — Stage 1 trajectory anomaly model.
- `models/clustering/` — scaffolded now, wired up in Stage 2.
- `training/` — training scripts and MLflow configs.
- `evaluation/` — precision/recall/F1 harness, built ahead of the model itself (Weeks 5-6) so every training run has a number to report.
