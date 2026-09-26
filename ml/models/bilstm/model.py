"""Small, interpretable next-position-delta Bi-LSTM for Stage 1."""
from __future__ import annotations

import torch
from torch import nn

DEFAULT_HIDDEN_SIZE = 64
VESSEL_CLASS_VOCABULARY = 101
VESSEL_CLASS_EMBEDDING_SIZE = 8


class BiLSTMNextDelta(nn.Module):
    """Predict a latitude/longitude delta; deltas remain meaningful across regions."""
    def __init__(self, feature_size: int, hidden_size: int = DEFAULT_HIDDEN_SIZE) -> None:
        super().__init__()
        self.vessel_classes = nn.Embedding(VESSEL_CLASS_VOCABULARY, VESSEL_CLASS_EMBEDDING_SIZE)
        self.encoder = nn.LSTM(feature_size + VESSEL_CLASS_EMBEDDING_SIZE, hidden_size, batch_first=True, bidirectional=True)
        self.output = nn.Linear(hidden_size * 2, 2)

    def forward(self, features: torch.Tensor, vessel_class: torch.Tensor) -> torch.Tensor:
        """Return one predicted next-state delta for each input timestep."""
        embeddings = self.vessel_classes(vessel_class.clamp(0, VESSEL_CLASS_VOCABULARY - 1))
        encoded, _ = self.encoder(torch.cat((features, embeddings), dim=-1))
        return self.output(encoded)
