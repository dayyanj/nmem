"""Embedding provider implementations."""

from nmem.providers.embedding.base import EmbeddingProvider
from nmem.providers.embedding.noop import NoOpEmbeddingProvider

__all__ = ["EmbeddingProvider", "NoOpEmbeddingProvider"]
