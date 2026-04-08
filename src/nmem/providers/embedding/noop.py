"""
No-op embedding provider — returns zero vectors.

Useful for testing or when semantic search is not needed.
All entries will have equal similarity (0.0) so search returns by recency/importance.
"""

from __future__ import annotations


class NoOpEmbeddingProvider:
    """Returns zero vectors. Disables semantic search while keeping all other features."""

    def __init__(self, dimensions: int = 384):
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        return [0.0] * self._dimensions

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dimensions for _ in texts]
