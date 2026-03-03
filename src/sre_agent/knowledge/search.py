"""Search for similar historical incidents.

Uses embeddings and vector search to find relevant past incidents.
"""

import logging

from sre_agent.intelligence.embeddings import EmbeddingGenerator, build_failure_text
from sre_agent.intelligence.vector_store import IncidentVectorStore
from sre_agent.schemas.intelligence import SimilarIncident
from sre_agent.schemas.knowledge import IncidentRecord

logger = logging.getLogger(__name__)


class IncidentSearch:
    """
    Search for similar historical incidents.

    Uses the vector store from the intelligence layer
    to find incidents with similar error patterns.
    """

    def __init__(
        self,
        vector_store: IncidentVectorStore | None = None,
        embedding_generator: EmbeddingGenerator | None = None,
        index_path: str | None = None,
    ):
        """
        Initialize incident search.

        Args:
            vector_store: Vector store instance
            embedding_generator: Embedding generator
            index_path: Path to store/load index
        """
        self.embedding_generator = embedding_generator or EmbeddingGenerator()

        if vector_store:
            self.vector_store = vector_store
        elif index_path:
            self.vector_store = IncidentVectorStore(index_path=index_path)
        else:
            self.vector_store = IncidentVectorStore()

    async def index_incident(self, incident: IncidentRecord) -> None:
        """
        Index an incident for future search.

        Args:
            incident: Incident to index
        """
        # Build text representation
        text = build_failure_text(
            error_messages=[incident.error_message or ""],
            stack_traces=[incident.hypothesis],
            changed_files=incident.affected_files,
            commit_message=None,
        )

        # Generate embedding
        embedding = self.embedding_generator.generate(text)

        # Add to vector store
        self.vector_store.add_incident(
            incident_id=str(incident.id),
            embedding=embedding,
            summary=incident.fix_summary or incident.hypothesis,
            root_cause=incident.hypothesis,
            resolution=incident.resolution,
            fix_diff=incident.fix_diff,
            occurred_at=incident.created_at,
        )

        logger.debug(f"Indexed incident: {incident.id}")

    async def search(
        self,
        error_message: str,
        stack_trace: str | None = None,
        changed_files: list[str] | None = None,
        k: int = 5,
    ) -> list[SimilarIncident]:
        """
        Search for similar incidents.

        Args:
            error_message: Error message to search for
            stack_trace: Optional stack trace
            changed_files: Optional list of changed files
            k: Number of results to return

        Returns:
            List of similar incidents
        """
        # Build query text
        text = build_failure_text(
            error_messages=[error_message],
            stack_traces=[stack_trace] if stack_trace else [],
            changed_files=changed_files or [],
        )

        # Generate embedding
        query_embedding = self.embedding_generator.generate(text)

        # Search vector store
        results = self.vector_store.search(query_embedding, k=k)

        # Convert to SimilarIncident
        similar = []
        for record, score in results:
            similar.append(
                SimilarIncident(
                    incident_id=record.incident_id,
                    similarity_score=score,
                    summary=record.summary,
                    root_cause=record.root_cause,
                    resolution=record.resolution,
                    fix_diff=record.fix_diff,
                    occurred_at=record.occurred_at,
                )
            )

        logger.info(
            f"Found {len(similar)} similar incidents",
            extra={"query": error_message[:50]},
        )

        return similar

    async def rebuild_index(
        self,
        incidents: list[IncidentRecord],
    ) -> int:
        """
        Rebuild the entire search index.

        Args:
            incidents: List of incidents to index

        Returns:
            Number of incidents indexed
        """
        # Create fresh vector store
        self.vector_store = IncidentVectorStore(
            index_path=str(self.vector_store.index_path) if self.vector_store.index_path else None
        )

        count = 0
        for incident in incidents:
            if incident.was_successful:  # Only index successful fixes
                await self.index_incident(incident)
                count += 1

        # Save index
        self.vector_store.save()

        logger.info(f"Rebuilt index with {count} incidents")
        return count

    def save(self) -> None:
        """Save the current index."""
        self.vector_store.save()

    def load(self) -> None:
        """Load the index from disk."""
        self.vector_store.load()
