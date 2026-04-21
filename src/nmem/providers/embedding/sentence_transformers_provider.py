"""
sentence-transformers embedding provider.

Runs locally — no API calls needed. Default model: all-MiniLM-L6-v2 (384 dims).
Model loading is lazy — deferred to first embed() call to avoid 2-3s startup
cost when embeddings aren't needed (e.g., nmem stats, nmem init).
"""

from __future__ import annotations

import logging

from nmem.exceptions import EmbeddingError

logger = logging.getLogger(__name__)


class SentenceTransformersProvider:
    """Local embedding provider using the sentence-transformers library.

    Args:
        model: Model name from HuggingFace (default: all-MiniLM-L6-v2).
        device: Device to run on ("cpu", "cuda", or None for auto-detect).
            Defaults to "cpu" since embedding models are small and CPU is
            always available, while GPU may be occupied by LLM inference.
    """

    def __init__(self, model: str = "all-MiniLM-L6-v2", device: str = "cpu"):
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            raise ImportError(
                "sentence-transformers package required. "
                "Install with: pip install nmem[st]"
            )

        self._model_name = model
        self._device = device
        self._model = None
        self._dimensions: int | None = None

    def _ensure_loaded(self):
        """Lazy-load the model on first use."""
        if self._model is not None:
            return
        import gc

        import torch
        from sentence_transformers import SentenceTransformer

        # Clean up any stale meta tensors from a previous instance in this
        # process — torch can leave model weights on the "meta" device after
        # garbage collection, causing "Cannot copy out of meta tensor" on reload.
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # Limit PyTorch thread pools BEFORE loading the model. Without this,
        # torch spawns 24+ threads per pool (intra-op + inter-op) and each
        # asyncio.to_thread worker inherits them — at 36 workers × 24 threads
        # = 860+ threads, causing severe GIL contention and ~25× slowdown on
        # sequential embedding calls.
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)

        logger.info("Loading embedding model: %s (device=%s)", self._model_name, self._device)
        try:
            self._model = SentenceTransformer(self._model_name, device=self._device)
        except Exception as e:
            raise EmbeddingError(
                f"Failed to load sentence-transformer model '{self._model_name}': {e}"
            ) from e
        self._dimensions = self._model.get_sentence_embedding_dimension()

    @property
    def dimensions(self) -> int:
        if self._dimensions is not None:
            return self._dimensions
        # Return default without loading model — config specifies dimensions
        return 384

    def embed(self, text: str) -> list[float]:
        self._ensure_loaded()
        try:
            embedding = self._model.encode(text, normalize_embeddings=True)
            return embedding.tolist()
        except Exception as e:
            raise EmbeddingError(f"Embedding failed: {e}") from e

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_loaded()
        try:
            embeddings = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
            return embeddings.tolist()
        except Exception as e:
            raise EmbeddingError(f"Batch embedding failed: {e}") from e
