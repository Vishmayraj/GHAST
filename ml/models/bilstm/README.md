# ml/models/bilstm/

Bi-LSTM sequence model: given a vessel's recent track, predicts its next plausible state. A prediction-vs-reported deviation beyond real physical limits is the core spoofing signal.

Trained and validated against the IEEE DataPort synthetic GPS spoofing dataset and the real-vs-simulated spoofed-track research (`data/research_datasets/`). Target: reproduce published precision/recall in the 0.9+ range.
