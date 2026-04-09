"""
No-op embedding provider — returns deterministic hash-based vectors.

Produces consistent, content-sensitive embeddings from a hash of the input text.
Semantically similar text produces similar (but not identical) vectors, making
dedup, hybrid search, and context threading work correctly in tests.
"""

from __future__ import annotations

import hashlib
import struct


class NoOpEmbeddingProvider:
    """Returns deterministic hash-based vectors for testing.

    Each word in the input contributes to the vector, so texts with shared
    words will have higher cosine similarity. This makes dedup checks,
    hybrid search ranking, and context threading behave realistically
    without requiring a real model.
    """

    def __init__(self, dimensions: int = 384):
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        """Generate a deterministic embedding from text content.

        Each word gets a unique position in the vector space determined by
        hashing the word to a set of dimension indices. Texts sharing words
        will share activated dimensions, producing high cosine similarity.
        """
        vec = [0.0] * self._dimensions
        words = text.lower().split()
        if not words:
            h = hashlib.sha256(b"empty").digest()
            for i in range(min(self._dimensions, 32)):
                vec[i] = (h[i % 32] - 128) / 256.0
            return self._normalize(vec)

        for word in words:
            # Each word activates ~20 dimensions with consistent values
            h = hashlib.sha256(word.encode("utf-8")).digest()
            seed = struct.unpack("<I", h[:4])[0]
            # Use the hash to pick which dimensions to activate
            for k in range(20):
                dim_hash = hashlib.md5(f"{word}_{k}".encode()).digest()
                dim_idx = struct.unpack("<H", dim_hash[:2])[0] % self._dimensions
                val = (struct.unpack("<B", dim_hash[2:3])[0] - 128) / 128.0
                vec[dim_idx] += val

        return self._normalize(vec)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    @staticmethod
    def _normalize(vec: list[float]) -> list[float]:
        """L2 normalize a vector."""
        norm = sum(x * x for x in vec) ** 0.5
        if norm < 1e-10:
            return vec
        return [x / norm for x in vec]
