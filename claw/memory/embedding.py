"""Embedding providers for semantic memory retrieval."""

from __future__ import annotations

import os
import warnings
from typing import Protocol


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""


class FastEmbedProvider:
    """Local FastEmbed-backed embedding provider.

    The dependency is imported lazily so Mini Claw can keep chatting and fall
    back to lexical search when the local embedding stack is not installed yet.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = (
            model_name
            or os.environ.get("MEMORY_EMBEDDING_MODEL")
            or DEFAULT_EMBEDDING_MODEL
        )
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise RuntimeError(
                    "fastembed is not installed; install it to enable local vector memory"
                ) from exc
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=UserWarning)
                self._model = TextEmbedding(model_name=self.model_name)
        return [
            [float(value) for value in vector]
            for vector in self._model.embed(texts)
        ]
