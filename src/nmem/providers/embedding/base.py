"""
Embedding provider protocol.

Embedding providers are synchronous — nmem wraps calls in asyncio.to_thread()
so they don't block the event loop.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for text embedding backends."""

    @property
    def dimensions(self) -> int:
        """Return the embedding vector dimensions."""
        ...

    def embed(self, text: str) -> list[float]:
        """Embed a single text string.

        This is synchronous — nmem wraps it in asyncio.to_thread().

        Args:
            text: Text to embed.

        Returns:
            Embedding vector as a list of floats.
        """
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a batch.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        ...
