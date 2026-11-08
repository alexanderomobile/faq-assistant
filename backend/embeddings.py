"""Эмбеддинги OpenAI. Вынесены отдельно, чтобы в тестах подменять их без сети."""
from __future__ import annotations

from typing import Any, Protocol

import numpy as np


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class OpenAIEmbedder:
    def __init__(self, client: Any, model: str, batch: int = 64):
        self.client = client
        self.model = model
        self.batch = batch

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            response = self.client.embeddings.create(model=self.model, input=texts[i:i + self.batch])
            vectors += [d.embedding for d in response.data]
        return np.array(vectors, dtype="float32")
