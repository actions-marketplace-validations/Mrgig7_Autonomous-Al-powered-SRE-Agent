"""Text embeddings for similarity search.

Uses sentence-transformers for generating embeddings of failure context.
"""

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Flag to track if embedding model is available
_EMBEDDING_MODEL_AVAILABLE = False
_embedding_model: Any = None


def _get_embedding_model() -> Any:
    """Lazy load the embedding model."""
    global _EMBEDDING_MODEL_AVAILABLE, _embedding_model

    if _embedding_model is not None:
        return _embedding_model

    try:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        _EMBEDDING_MODEL_AVAILABLE = True
        logger.info("Loaded sentence-transformers model")
        return _embedding_model
    except ImportError:
        logger.warning(
            "sentence-transformers not installed. "
            "Install with: pip install sentence-transformers"
        )
        _EMBEDDING_MODEL_AVAILABLE = False
        return None
    except Exception as e:
        logger.error(f"Failed to load embedding model: {e}")
        _EMBEDDING_MODEL_AVAILABLE = False
        return None


class EmbeddingGenerator:
    """
    Generates text embeddings for failure context.

    Uses sentence-transformers/all-MiniLM-L6-v2 by default.
    Falls back to simple TF-IDF-like hashing if model not available.
    """

    EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 dimension

    def __init__(self) -> None:
        self._model = _get_embedding_model()

    @property
    def is_model_available(self) -> bool:
        """Check if the embedding model is available."""
        return self._model is not None

    def generate(self, text: str) -> np.ndarray:
        """
        Generate embedding for text.

        Args:
            text: Text to embed

        Returns:
            Numpy array of shape (384,)
        """
        if self._model is not None:
            embedding = self._model.encode(text, convert_to_numpy=True)
            return embedding.astype(np.float32)
        else:
            # Fallback: simple hash-based embedding
            return self._fallback_embedding(text)

    def generate_batch(self, texts: list[str]) -> np.ndarray:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed

        Returns:
            Numpy array of shape (n, 384)
        """
        if self._model is not None:
            embeddings = self._model.encode(texts, convert_to_numpy=True)
            return embeddings.astype(np.float32)
        else:
            return np.array([self._fallback_embedding(t) for t in texts])

    def _fallback_embedding(self, text: str) -> np.ndarray:
        """
        Fallback embedding using character n-gram hashing.

        Not as good as transformer embeddings but provides basic functionality.
        """
        # Simple hash-based embedding
        embedding = np.zeros(self.EMBEDDING_DIM, dtype=np.float32)

        # Use character n-grams
        text_lower = text.lower()
        for n in [2, 3, 4]:
            for i in range(len(text_lower) - n + 1):
                ngram = text_lower[i : i + n]
                # Hash to position
                h = hash(ngram) % self.EMBEDDING_DIM
                embedding[h] += 1.0

        # Normalize
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding


def build_failure_text(
    error_messages: list[str],
    stack_traces: list[str],
    changed_files: list[str],
    commit_message: str | None = None,
) -> str:
    """
    Build text representation of a failure for embedding.

    Combines error messages, stack traces, and changed files into
    a single text suitable for similarity comparison.
    """
    parts = []

    if error_messages:
        parts.append("Errors: " + " | ".join(error_messages[:5]))

    if stack_traces:
        # Summarize stack traces (first frame of each)
        parts.append("Stack: " + " | ".join(stack_traces[:3]))

    if changed_files:
        parts.append("Files: " + ", ".join(changed_files[:10]))

    if commit_message:
        parts.append("Commit: " + commit_message[:200])

    return " ".join(parts)
