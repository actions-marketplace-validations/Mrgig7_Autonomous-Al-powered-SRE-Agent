"""FAISS-based vector store for incident similarity search.

Stores embeddings of historical incidents for similarity matching.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Check if FAISS is available
try:
    import faiss

    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.warning("FAISS not installed. Install with: pip install faiss-cpu")


@dataclass
class IncidentRecord:
    """Stored incident metadata."""

    incident_id: str
    summary: str
    root_cause: str | None
    resolution: str | None
    fix_diff: str | None
    occurred_at: datetime | None


class IncidentVectorStore:
    """
    FAISS-based vector store for incident similarity search.

    Stores embeddings alongside incident metadata for retrieval.
    Falls back to brute-force search if FAISS is not available.
    """

    def __init__(
        self,
        index_path: str | None = None,
        dimension: int = 384,
    ):
        """
        Initialize vector store.

        Args:
            index_path: Path to persist index (optional)
            dimension: Embedding dimension (default: 384 for all-MiniLM-L6-v2)
        """
        self.dimension = dimension
        self.index_path = Path(index_path) if index_path else None

        # Initialize storage
        self.incidents: dict[str, IncidentRecord] = {}
        self.id_to_idx: dict[str, int] = {}
        self.idx_to_id: dict[int, str] = {}

        if FAISS_AVAILABLE:
            self.index = faiss.IndexFlatL2(dimension)
            logger.info("Initialized FAISS index")
        else:
            self.index = None
            self._embeddings: list[np.ndarray] = []
            logger.warning("Using fallback brute-force search (FAISS not available)")

        # Load existing index if path provided
        if self.index_path and self.index_path.exists():
            self.load()

    def add_incident(
        self,
        incident_id: str,
        embedding: np.ndarray,
        summary: str,
        root_cause: str | None = None,
        resolution: str | None = None,
        fix_diff: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """
        Add an incident to the store.

        Args:
            incident_id: Unique incident identifier
            embedding: Embedding vector
            summary: Brief summary of the incident
            root_cause: Known root cause
            resolution: How it was resolved
            fix_diff: Fix diff if available
            occurred_at: When the incident occurred
        """
        # Store metadata
        self.incidents[incident_id] = IncidentRecord(
            incident_id=incident_id,
            summary=summary,
            root_cause=root_cause,
            resolution=resolution,
            fix_diff=fix_diff,
            occurred_at=occurred_at,
        )

        # Store id mapping
        idx = len(self.id_to_idx)
        self.id_to_idx[incident_id] = idx
        self.idx_to_id[idx] = incident_id

        # Add to index
        embedding = embedding.reshape(1, -1).astype(np.float32)

        if FAISS_AVAILABLE and self.index is not None:
            self.index.add(embedding)
        else:
            self._embeddings.append(embedding.flatten())

        logger.debug(f"Added incident {incident_id} to vector store")

    def search(
        self,
        query: np.ndarray,
        k: int = 5,
    ) -> list[tuple[IncidentRecord, float]]:
        """
        Search for similar incidents.

        Args:
            query: Query embedding vector
            k: Number of results to return

        Returns:
            List of (IncidentRecord, similarity_score) tuples
        """
        if len(self.incidents) == 0:
            return []

        query = query.reshape(1, -1).astype(np.float32)

        if FAISS_AVAILABLE and self.index is not None:
            # Use FAISS
            distances, indices = self.index.search(query, min(k, len(self.incidents)))
            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0:
                    continue
                incident_id = self.idx_to_id.get(int(idx))
                if incident_id and incident_id in self.incidents:
                    # Convert L2 distance to similarity score (0-1)
                    similarity = 1.0 / (1.0 + float(dist))
                    results.append((self.incidents[incident_id], similarity))
            return results
        else:
            # Fallback: brute force search
            if not self._embeddings:
                return []

            embeddings_matrix = np.array(self._embeddings)
            distances = np.linalg.norm(embeddings_matrix - query.flatten(), axis=1)
            indices = np.argsort(distances)[:k]

            results = []
            for idx in indices:
                incident_id = self.idx_to_id.get(int(idx))
                if incident_id and incident_id in self.incidents:
                    similarity = 1.0 / (1.0 + float(distances[idx]))
                    results.append((self.incidents[incident_id], similarity))
            return results

    def save(self) -> None:
        """Save index and metadata to disk."""
        if not self.index_path:
            logger.warning("No index path configured, cannot save")
            return

        self.index_path.mkdir(parents=True, exist_ok=True)

        # Save FAISS index
        if FAISS_AVAILABLE and self.index is not None:
            faiss.write_index(self.index, str(self.index_path / "index.faiss"))
        else:
            # Save embeddings as numpy
            if self._embeddings:
                np.save(
                    self.index_path / "embeddings.npy",
                    np.array(self._embeddings),
                )

        # Save metadata
        metadata = {
            "incidents": {
                k: {
                    "incident_id": v.incident_id,
                    "summary": v.summary,
                    "root_cause": v.root_cause,
                    "resolution": v.resolution,
                    "fix_diff": v.fix_diff,
                    "occurred_at": v.occurred_at.isoformat() if v.occurred_at else None,
                }
                for k, v in self.incidents.items()
            },
            "id_to_idx": self.id_to_idx,
            "dimension": self.dimension,
        }

        with open(self.index_path / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Saved vector store to {self.index_path}")

    def load(self) -> None:
        """Load index and metadata from disk."""
        if not self.index_path or not self.index_path.exists():
            return

        # Load FAISS index
        faiss_path = self.index_path / "index.faiss"
        npy_path = self.index_path / "embeddings.npy"

        if FAISS_AVAILABLE and faiss_path.exists():
            self.index = faiss.read_index(str(faiss_path))
        elif npy_path.exists():
            self._embeddings = list(np.load(npy_path))

        # Load metadata
        metadata_path = self.index_path / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path) as f:
                metadata = json.load(f)

            self.dimension = metadata.get("dimension", 384)
            self.id_to_idx = metadata.get("id_to_idx", {})
            self.idx_to_id = {int(v): k for k, v in self.id_to_idx.items()}

            for k, v in metadata.get("incidents", {}).items():
                self.incidents[k] = IncidentRecord(
                    incident_id=v["incident_id"],
                    summary=v["summary"],
                    root_cause=v.get("root_cause"),
                    resolution=v.get("resolution"),
                    fix_diff=v.get("fix_diff"),
                    occurred_at=(
                        datetime.fromisoformat(v["occurred_at"]) if v.get("occurred_at") else None
                    ),
                )

        logger.info(f"Loaded vector store from {self.index_path}")

    @property
    def size(self) -> int:
        """Return number of incidents in store."""
        return len(self.incidents)
