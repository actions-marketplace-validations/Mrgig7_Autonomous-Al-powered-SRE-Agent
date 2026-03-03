"""Root Cause Analysis Engine.

Combines classification, similarity search, and context analysis
to generate root cause hypotheses.
"""

import logging
import time

from sre_agent.intelligence.classifier import FailureClassifier
from sre_agent.intelligence.embeddings import EmbeddingGenerator, build_failure_text
from sre_agent.intelligence.vector_store import IncidentVectorStore
from sre_agent.schemas.context import FailureContextBundle
from sre_agent.schemas.intelligence import (
    AffectedFile,
    Classification,
    FailureCategory,
    RCAHypothesis,
    RCAResult,
    SimilarIncident,
)

logger = logging.getLogger(__name__)


class RCAEngine:
    """
    Root Cause Analysis Engine.

    Analyzes failure context to generate root cause hypotheses
    with confidence scores and suggested fixes.
    """

    def __init__(
        self,
        classifier: FailureClassifier | None = None,
        embedding_generator: EmbeddingGenerator | None = None,
        vector_store: IncidentVectorStore | None = None,
    ):
        """
        Initialize RCA engine.

        Args:
            classifier: Failure classifier instance
            embedding_generator: Embedding generator for similarity search
            vector_store: Vector store for historical incidents
        """
        self.classifier = classifier or FailureClassifier()
        self.embeddings = embedding_generator or EmbeddingGenerator()
        self.vector_store = vector_store

    def analyze(self, context: FailureContextBundle) -> RCAResult:
        """
        Analyze failure context and generate root cause hypothesis.

        Args:
            context: Failure context bundle

        Returns:
            RCAResult with classification, hypothesis, and suggestions
        """
        start_time = time.time()

        logger.info(
            "Starting RCA analysis",
            extra={"event_id": str(context.event_id), "repo": context.repo},
        )

        # Step 1: Classify the failure
        classification = self.classifier.classify(context)

        # Step 2: Analyze affected files
        affected_files = self._analyze_affected_files(context, classification)

        # Step 3: Search for similar incidents
        similar_incidents = self._search_similar_incidents(context)

        # Step 4: Generate hypotheses
        primary_hypothesis, alternatives = self._generate_hypotheses(
            context, classification, affected_files, similar_incidents
        )

        # Step 5: Generate fix patterns
        suggested_patterns = self._generate_fix_patterns(classification, similar_incidents)

        analysis_time = time.time() - start_time

        result = RCAResult(
            event_id=context.event_id,
            classification=classification,
            primary_hypothesis=primary_hypothesis,
            alternative_hypotheses=alternatives,
            affected_files=affected_files,
            similar_incidents=similar_incidents,
            suggested_patterns=suggested_patterns,
            analysis_time_seconds=analysis_time,
        )

        logger.info(
            "RCA analysis complete",
            extra={
                "event_id": str(context.event_id),
                "category": classification.category.value,
                "confidence": classification.confidence,
                "hypothesis_confidence": primary_hypothesis.confidence,
                "similar_incidents": len(similar_incidents),
                "analysis_time": analysis_time,
            },
        )

        return result

    def _analyze_affected_files(
        self,
        context: FailureContextBundle,
        classification: Classification,
    ) -> list[AffectedFile]:
        """Analyze which files are likely related to the failure."""
        affected = []

        # Files from stack traces are highly relevant
        stack_trace_files = set()
        for trace in context.stack_traces:
            for frame in trace.frames:
                if frame.file and not self._is_library_file(frame.file):
                    stack_trace_files.add(frame.file)

        for file in stack_trace_files:
            affected.append(
                AffectedFile(
                    filename=file,
                    relevance_score=0.9,
                    reason="Appears in stack trace",
                    is_in_stack_trace=True,
                    is_recently_changed=file in [f.filename for f in context.changed_files],
                    suggested_action="Review error handling at this location",
                )
            )

        # Changed files that might be related
        for changed in context.changed_files:
            if changed.filename in stack_trace_files:
                continue  # Already added

            relevance = self._calculate_file_relevance(changed.filename, classification, context)
            if relevance > 0.3:
                affected.append(
                    AffectedFile(
                        filename=changed.filename,
                        relevance_score=relevance,
                        reason="Recently changed",
                        is_in_stack_trace=False,
                        is_recently_changed=True,
                        suggested_action=self._suggest_file_action(
                            changed.filename, classification
                        ),
                    )
                )

        # Sort by relevance
        affected.sort(key=lambda x: x.relevance_score, reverse=True)
        return affected[:10]  # Limit to top 10

    def _search_similar_incidents(
        self,
        context: FailureContextBundle,
    ) -> list[SimilarIncident]:
        """Search for similar historical incidents."""
        if self.vector_store is None or self.vector_store.size == 0:
            return []

        # Build text representation for embedding
        error_messages = [e.message for e in context.errors]
        stack_summaries = [f"{t.exception_type}: {t.message}" for t in context.stack_traces]
        changed_filenames = [f.filename for f in context.changed_files]

        text = build_failure_text(
            error_messages=error_messages,
            stack_traces=stack_summaries,
            changed_files=changed_filenames,
            commit_message=context.commit_message,
        )

        # Generate embedding and search
        embedding = self.embeddings.generate(text)
        results = self.vector_store.search(embedding, k=5)

        similar = []
        for record, score in results:
            if score >= 0.3:  # Minimum similarity threshold
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

        return similar

    def _generate_hypotheses(
        self,
        context: FailureContextBundle,
        classification: Classification,
        affected_files: list[AffectedFile],
        similar_incidents: list[SimilarIncident],
    ) -> tuple[RCAHypothesis, list[RCAHypothesis]]:
        """Generate root cause hypotheses."""
        hypotheses = []

        # Base hypothesis from classification
        primary_desc = self._generate_hypothesis_description(
            context, classification, affected_files
        )
        primary_evidence = self._gather_evidence(context, classification)

        primary = RCAHypothesis(
            description=primary_desc,
            confidence=classification.confidence,
            evidence=primary_evidence,
            suggested_fix=self._suggest_fix(classification, context),
        )
        hypotheses.append(primary)

        # Add hypothesis from similar incidents if available
        if similar_incidents:
            best_match = similar_incidents[0]
            if best_match.root_cause and best_match.similarity_score >= 0.7:
                hypotheses.append(
                    RCAHypothesis(
                        description=f"Similar to past incident: {best_match.root_cause}",
                        confidence=best_match.similarity_score * 0.9,
                        evidence=[
                            f"Similar incident: {best_match.summary}",
                            f"Similarity score: {best_match.similarity_score:.2f}",
                        ],
                        suggested_fix=best_match.resolution,
                    )
                )

        # Add secondary hypothesis if available
        if classification.secondary_category:
            secondary_desc = self._get_category_description(classification.secondary_category)
            hypotheses.append(
                RCAHypothesis(
                    description=secondary_desc,
                    confidence=classification.confidence * 0.7,
                    evidence=["Secondary pattern detected"],
                )
            )

        # Sort by confidence and return primary + alternatives
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses[0], hypotheses[1:4]

    def _generate_hypothesis_description(
        self,
        context: FailureContextBundle,
        classification: Classification,
        affected_files: list[AffectedFile],
    ) -> str:
        """Generate a human-readable hypothesis description."""
        category = classification.category

        # Start with base description
        base = self._get_category_description(category)

        # Add specifics from context
        if context.stack_traces:
            trace = context.stack_traces[0]
            base += f" The {trace.exception_type} occurred"
            if trace.frames:
                frame = trace.frames[0]
                base += f" in {frame.file}"
                if frame.function:
                    base += f" ({frame.function})"

        if affected_files:
            top_file = affected_files[0]
            if top_file.is_recently_changed:
                base += f". Recent changes to {top_file.filename} may be related."

        return base

    def _get_category_description(self, category: FailureCategory) -> str:
        """Get base description for a category."""
        descriptions = {
            FailureCategory.INFRASTRUCTURE: (
                "Infrastructure issue detected (resource exhaustion or CI system failure)"
            ),
            FailureCategory.DEPENDENCY: (
                "Dependency issue detected (missing or incompatible package)"
            ),
            FailureCategory.CODE: ("Code error detected (type error, logic error, or bug)"),
            FailureCategory.CONFIGURATION: (
                "Configuration issue detected (missing variable or invalid config)"
            ),
            FailureCategory.TEST: ("Test assertion failure (test logic or assertion issue)"),
            FailureCategory.FLAKY: (
                "Potentially flaky failure (timeout or non-deterministic behavior)"
            ),
            FailureCategory.SECURITY: ("Security scan failure (vulnerability detected)"),
            FailureCategory.UNKNOWN: ("Unable to determine specific cause"),
        }
        return descriptions.get(category, "Failure detected")

    def _gather_evidence(
        self,
        context: FailureContextBundle,
        classification: Classification,
    ) -> list[str]:
        """Gather evidence supporting the hypothesis."""
        evidence = []

        # Add classification indicators
        for indicator in classification.indicators[:3]:
            evidence.append(f"Pattern matched: {indicator}")

        # Add error messages
        for error in context.errors[:2]:
            evidence.append(f"Error: {error.message[:100]}")

        # Add stack trace info
        if context.stack_traces:
            trace = context.stack_traces[0]
            evidence.append(f"Exception: {trace.exception_type}: {trace.message[:100]}")

        return evidence

    def _suggest_fix(
        self,
        classification: Classification,
        context: FailureContextBundle,
    ) -> str | None:
        """Suggest a fix based on classification."""
        suggestions = {
            FailureCategory.INFRASTRUCTURE: ("Retry the job or check CI infrastructure status"),
            FailureCategory.DEPENDENCY: ("Check package versions and update dependencies"),
            FailureCategory.CODE: ("Review the error location and add proper error handling"),
            FailureCategory.CONFIGURATION: ("Verify all required environment variables are set"),
            FailureCategory.TEST: ("Review test assertions and expected values"),
            FailureCategory.FLAKY: ("Consider adding retries or investigating timing issues"),
            FailureCategory.SECURITY: ("Review and remediate the security vulnerability"),
        }
        return suggestions.get(classification.category)

    def _generate_fix_patterns(
        self,
        classification: Classification,
        similar_incidents: list[SimilarIncident],
    ) -> list[str]:
        """Generate suggested fix patterns."""
        patterns = []

        # Patterns from similar incidents
        for incident in similar_incidents:
            if incident.resolution and incident.similarity_score >= 0.6:
                patterns.append(incident.resolution)

        # Generic patterns by category
        category_patterns = {
            FailureCategory.DEPENDENCY: [
                "Run dependency update",
                "Pin dependency versions",
                "Clear dependency cache",
            ],
            FailureCategory.CONFIGURATION: [
                "Add missing environment variable",
                "Update configuration file",
                "Verify secrets are available",
            ],
            FailureCategory.CODE: [
                "Add null/undefined check",
                "Fix type mismatch",
                "Handle edge case",
            ],
        }

        if classification.category in category_patterns:
            patterns.extend(category_patterns[classification.category])

        return list(set(patterns))[:5]  # Dedupe and limit

    def _is_library_file(self, filepath: str) -> bool:
        """Check if file is likely a library/vendor file."""
        library_patterns = [
            "node_modules",
            "site-packages",
            "vendor",
            ".venv",
            "dist-packages",
            "/usr/lib",
            "/usr/local/lib",
        ]
        return any(p in filepath for p in library_patterns)

    def _calculate_file_relevance(
        self,
        filename: str,
        classification: Classification,
        context: FailureContextBundle,
    ) -> float:
        """Calculate relevance score for a changed file."""
        relevance = 0.3  # Base relevance for any changed file

        # Higher relevance for certain file types based on category
        if classification.category == FailureCategory.DEPENDENCY:
            if any(
                p in filename for p in ["package.json", "requirements.txt", "Cargo.toml", "go.mod"]
            ):
                relevance += 0.5
        elif classification.category == FailureCategory.CONFIGURATION:
            if any(p in filename for p in [".env", "config", ".yml", ".yaml", ".json"]):
                relevance += 0.4
        elif classification.category == FailureCategory.TEST:
            if "test" in filename.lower():
                relevance += 0.4

        # Check if file extension matches stack traces
        for trace in context.stack_traces:
            for frame in trace.frames:
                if frame.file and frame.file.endswith(filename.split(".")[-1]):
                    relevance += 0.2
                    break

        return min(relevance, 1.0)

    def _suggest_file_action(
        self,
        filename: str,
        classification: Classification,
    ) -> str | None:
        """Suggest action for a specific file."""
        if classification.category == FailureCategory.DEPENDENCY:
            if "package.json" in filename:
                return "Check npm dependencies"
            if "requirements" in filename:
                return "Verify Python packages"
        if classification.category == FailureCategory.CONFIGURATION:
            return "Review configuration values"
        return "Review recent changes"
