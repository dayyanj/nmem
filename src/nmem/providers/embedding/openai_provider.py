"""
OpenAI embedding provider.

Supports text-embedding-3-small (1536 dims), text-embedding-3-large (3072 dims),
and text-embedding-ada-002 (1536 dims).
"""

from __future__ import annotations

import logging

from nmem.exceptions import EmbeddingError

logger = logging.getLogger(__name__)


class OpenAIEmbeddingProvider:
    """Cloud embedding provider using the OpenAI API.

    Args:
        model: OpenAI embedding model name.
        dimensions: Override output dimensions (supported by text-embedding-3-*).
        api_key: OpenAI API key.
        base_url: Base URL override for compatible APIs.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package required for OpenAI embedding provider. "
                "Install with: pip install nmem[openai]"
            )

        self._model = model
        self._dimensions = dimensions
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        try:
            response = self._client.embeddings.create(
                model=self._model,
                input=text,
                dimensions=self._dimensions,
            )
            return response.data[0].embedding
        except Exception as e:
            raise EmbeddingError(f"OpenAI embedding failed: {e}") from e

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(
                model=self._model,
                input=texts,
                dimensions=self._dimensions,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            raise EmbeddingError(f"OpenAI batch embedding failed: {e}") from e
