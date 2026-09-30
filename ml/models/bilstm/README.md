# ml/models/bilstm/

Bi-LSTM sequence model: given a vessel's recent track, predicts its next plausible state. A prediction-vs-reported deviation beyond real physical limits is the core spoofing signal.

Trained on 20-report windows of MarineCadastre historical AIS from the database (`docs/ml-pipeline.md`), and evaluated on synthetic spoofs injected into held-out windows (`docs/scoring-and-evaluation.md`). The IEEE DataPort dataset in `data/research_datasets/` is used only by the evaluation harness smoke test. The 0.9+ precision/recall target from the plan has not been reached; the recorded F1 is 0.424.
