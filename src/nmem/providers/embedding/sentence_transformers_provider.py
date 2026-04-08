"""
sentence-transformers embedding provider.

Runs locally — no API calls needed. Default model: all-MiniLM-L6-v2 (384 dims).
"""

from __future__ import annotations

import logging

from nmem.exceptions import EmbeddingError

logger = logging.getLogger(__name__)


class SentenceTransformersProvider:
    """Local embedding provider using the sentence-transformers library.

    Args:
        model: Model name from HuggingFace (default: all-MiniLM-L6-v2).
    """

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers package required. "
                "Install with: pip install nmem[st]"
            )

        self._model_name = model
        try:
            self._model = SentenceTransformer(model)
        except Exception as e:
            raise EmbeddingError(f"Failed to load sentence-transformer model '{model}': {e}") from e

        self._dimensions = self._model.get_sentence_embedding_dimension()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        try:
            embedding = self._model.encode(text, normalize_embeddings=True)
            return embedding.tolist()
        except Exception as e:
            raise EmbeddingError(f"Embedding failed: {e}") from e

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            embeddings = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
            return embeddings.tolist()
        except Exception as e:
            raise EmbeddingError(f"Batch embedding failed: {e}") from e
