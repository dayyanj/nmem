"""
nmem exception hierarchy.

All nmem exceptions inherit from NmemError so callers can catch broadly
or handle specific cases.
"""


class NmemError(Exception):
    """Base exception for all nmem errors."""


class ConfigError(NmemError):
    """Invalid or missing configuration."""


class StorageError(NmemError):
    """Database or storage backend failure."""


class EmbeddingError(NmemError):
    """Embedding provider failure."""


class LLMError(NmemError):
    """LLM provider failure (compression, synthesis, etc.)."""


class PermissionError(NmemError):
    """Agent lacks permission for the requested operation."""


class ConflictError(NmemError):
    """Memory conflict detected between records."""

    def __init__(self, message: str, record_a_id: int | None = None, record_b_id: int | None = None):
        super().__init__(message)
        self.record_a_id = record_a_id
        self.record_b_id = record_b_id


class TierError(NmemError):
    """Error in a specific memory tier operation."""

    def __init__(self, message: str, tier: str | None = None):
        super().__init__(message)
        self.tier = tier


class DimensionMismatchError(ConfigError):
    """Embedding dimensions don't match stored data."""

    def __init__(self, expected: int, got: int):
        super().__init__(
            f"Embedding dimension mismatch: database has {expected}-dim vectors, "
            f"but configured provider produces {got}-dim vectors. "
            f"Change the embedding provider or re-embed existing data."
        )
        self.expected = expected
        self.got = got
