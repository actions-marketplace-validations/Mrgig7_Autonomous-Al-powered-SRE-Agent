"""Rule-based failure classifier.

Analyzes failure context bundles to classify failures into categories
with confidence scores.
"""

import logging
import re

from sre_agent.schemas.context import FailureContextBundle
from sre_agent.schemas.intelligence import Classification, FailureCategory

logger = logging.getLogger(__name__)


class ClassificationRule:
    """A single classification rule."""

    def __init__(
        self,
        name: str,
        category: FailureCategory,
        patterns: list[str],
        confidence: float,
        reason: str,
    ):
        self.name = name
        self.category = category
        self.patterns = [re.compile(p, re.IGNORECASE) for p in patterns]
        self.confidence = confidence
        self.reason = reason

    def matches(self, text: str) -> list[str]:
        """Return list of matched patterns."""
        matches = []
        for pattern in self.patterns:
            if pattern.search(text):
                matches.append(pattern.pattern)
        return matches


class FailureClassifier:
    """
    Rule-based failure classification.

    Analyzes failure context to determine the category of failure
    (infrastructure, dependency, code, configuration, test, flaky).
    """

    def __init__(self) -> None:
        self.rules = self._build_rules()

    def _build_rules(self) -> list[ClassificationRule]:
        """Build classification rules."""
        return [
            # Infrastructure failures
            ClassificationRule(
                name="memory_exhaustion",
                category=FailureCategory.INFRASTRUCTURE,
                patterns=[
                    r"out\s*of\s*memory",
                    r"oom\s*kill",
                    r"memory\s*allocation\s*failed",
                    r"java\.lang\.OutOfMemoryError",
                    r"cannot\s*allocate\s*memory",
                    r"killed.*memory",
                ],
                confidence=0.95,
                reason="Memory exhaustion detected",
            ),
            ClassificationRule(
                name="disk_exhaustion",
                category=FailureCategory.INFRASTRUCTURE,
                patterns=[
                    r"no\s*space\s*left",
                    r"disk\s*full",
                    r"ENOSPC",
                    r"insufficient\s*disk\s*space",
                ],
                confidence=0.95,
                reason="Disk space exhaustion detected",
            ),
            ClassificationRule(
                name="network_failure",
                category=FailureCategory.INFRASTRUCTURE,
                patterns=[
                    r"connection\s*refused",
                    r"connection\s*timed?\s*out",
                    r"ECONNREFUSED",
                    r"ETIMEDOUT",
                    r"network\s*unreachable",
                    r"DNS\s*lookup\s*failed",
                    r"could\s*not\s*resolve\s*host",
                ],
                confidence=0.85,
                reason="Network connectivity issue detected",
            ),
            ClassificationRule(
                name="ci_runner_issue",
                category=FailureCategory.INFRASTRUCTURE,
                patterns=[
                    r"runner\s*failed",
                    r"runner\s*system\s*failure",
                    r"job\s*was\s*terminated",
                    r"runner\s*took\s*too\s*long",
                ],
                confidence=0.90,
                reason="CI runner infrastructure issue",
            ),
            # Dependency failures
            ClassificationRule(
                name="python_import_error",
                category=FailureCategory.DEPENDENCY,
                patterns=[
                    r"ModuleNotFoundError",
                    r"ImportError",
                    r"No\s*module\s*named",
                    r"cannot\s*import\s*name",
                ],
                confidence=0.90,
                reason="Python import/dependency error",
            ),
            ClassificationRule(
                name="npm_dependency",
                category=FailureCategory.DEPENDENCY,
                patterns=[
                    r"npm\s*ERR!.*peer\s*dep",
                    r"npm\s*ERR!.*ERESOLVE",
                    r"Cannot\s*find\s*module",
                    r"Module\s*not\s*found",
                    r"Could\s*not\s*resolve\s*dependency",
                ],
                confidence=0.90,
                reason="NPM dependency resolution error",
            ),
            ClassificationRule(
                name="version_conflict",
                category=FailureCategory.DEPENDENCY,
                patterns=[
                    r"version\s*conflict",
                    r"incompatible\s*version",
                    r"requires.*but.*found",
                    r"version\s*mismatch",
                ],
                confidence=0.85,
                reason="Version conflict detected",
            ),
            # Configuration failures
            ClassificationRule(
                name="missing_env_var",
                category=FailureCategory.CONFIGURATION,
                patterns=[
                    r"environment\s*variable.*not\s*set",
                    r"missing\s*env",
                    r"KeyError:.*['\"]?[A-Z_]+['\"]?",
                    r"undefined\s*variable",
                    r"required.*not\s*provided",
                ],
                confidence=0.90,
                reason="Missing environment variable or configuration",
            ),
            ClassificationRule(
                name="config_parse_error",
                category=FailureCategory.CONFIGURATION,
                patterns=[
                    r"YAML\s*parse\s*error",
                    r"JSON\s*parse\s*error",
                    r"invalid\s*configuration",
                    r"config.*validation.*failed",
                ],
                confidence=0.85,
                reason="Configuration parsing error",
            ),
            ClassificationRule(
                name="permission_denied",
                category=FailureCategory.CONFIGURATION,
                patterns=[
                    r"permission\s*denied",
                    r"access\s*denied",
                    r"EACCES",
                    r"insufficient\s*permissions",
                    r"403\s*Forbidden",
                    r"401\s*Unauthorized",
                ],
                confidence=0.85,
                reason="Permission or access configuration issue",
            ),
            # Code failures
            ClassificationRule(
                name="type_error",
                category=FailureCategory.CODE,
                patterns=[
                    r"TypeError",
                    r"AttributeError",
                    r"undefined\s*is\s*not\s*a\s*function",
                    r"Cannot\s*read\s*propert",
                    r"NullPointerException",
                    r"nil\s*pointer\s*dereference",
                ],
                confidence=0.85,
                reason="Type or null reference error",
            ),
            ClassificationRule(
                name="logic_error",
                category=FailureCategory.CODE,
                patterns=[
                    r"IndexError",
                    r"KeyError(?!:.*[A-Z_]{3,})",  # Exclude env var patterns
                    r"index\s*out\s*of\s*(?:range|bounds)",
                    r"ArrayIndexOutOfBoundsException",
                    r"panic:.*index\s*out\s*of\s*range",
                ],
                confidence=0.80,
                reason="Logic or indexing error",
            ),
            ClassificationRule(
                name="syntax_error",
                category=FailureCategory.CODE,
                patterns=[
                    r"SyntaxError",
                    r"unexpected\s*token",
                    r"parse\s*error",
                    r"compilation\s*failed",
                ],
                confidence=0.90,
                reason="Syntax or compilation error",
            ),
            # Test failures
            ClassificationRule(
                name="assertion_failure",
                category=FailureCategory.TEST,
                patterns=[
                    r"AssertionError",
                    r"assert\s*.*failed",
                    r"expected.*but\s*got",
                    r"expect\(.*\)\.to",
                    r"FAILED\s+test",
                ],
                confidence=0.85,
                reason="Test assertion failure",
            ),
            # Flaky patterns
            ClassificationRule(
                name="timeout",
                category=FailureCategory.FLAKY,
                patterns=[
                    r"timed?\s*out",
                    r"deadline\s*exceeded",
                    r"operation\s*timed?\s*out",
                ],
                confidence=0.70,
                reason="Timeout - possibly flaky",
            ),
            ClassificationRule(
                name="race_condition",
                category=FailureCategory.FLAKY,
                patterns=[
                    r"race\s*condition",
                    r"concurrent\s*modification",
                    r"deadlock",
                ],
                confidence=0.75,
                reason="Possible race condition or concurrency issue",
            ),
            # Security failures
            ClassificationRule(
                name="security_scan",
                category=FailureCategory.SECURITY,
                patterns=[
                    r"vulnerability\s*found",
                    r"CVE-\d{4}-\d+",
                    r"security\s*scan\s*failed",
                    r"high\s*severity\s*issue",
                ],
                confidence=0.95,
                reason="Security scan failure",
            ),
        ]

    def classify(self, context: FailureContextBundle) -> Classification:
        """
        Classify a failure based on its context.

        Args:
            context: The failure context bundle to analyze

        Returns:
            Classification with category, confidence, and reasoning
        """
        # Build searchable text from context
        search_text = self._build_search_text(context)

        # Find matching rules
        matches: list[tuple[ClassificationRule, list[str]]] = []

        for rule in self.rules:
            matched_patterns = rule.matches(search_text)
            if matched_patterns:
                matches.append((rule, matched_patterns))

        if not matches:
            return Classification(
                category=FailureCategory.UNKNOWN,
                confidence=0.0,
                reasoning="No classification patterns matched",
                indicators=[],
            )

        # Sort by confidence and take the best match
        matches.sort(key=lambda x: x[0].confidence, reverse=True)
        best_rule, indicators = matches[0]

        # Check for secondary category
        secondary = None
        if len(matches) > 1:
            second_rule, _ = matches[1]
            if second_rule.category != best_rule.category:
                secondary = second_rule.category

        logger.info(
            "Classified failure",
            extra={
                "category": best_rule.category.value,
                "confidence": best_rule.confidence,
                "rule": best_rule.name,
            },
        )

        return Classification(
            category=best_rule.category,
            confidence=best_rule.confidence,
            reasoning=best_rule.reason,
            indicators=indicators,
            secondary_category=secondary,
        )

    def _build_search_text(self, context: FailureContextBundle) -> str:
        """Build searchable text from context."""
        parts = []

        # Add error messages
        for error in context.errors:
            parts.append(error.message)

        # Add stack trace info
        for trace in context.stack_traces:
            parts.append(f"{trace.exception_type}: {trace.message}")

        # Add test failures
        for failure in context.test_failures:
            parts.append(failure.error_message)

        # Add build errors
        for error in context.build_errors:
            parts.append(error.message)

        # Add log summary if available
        if context.log_summary:
            parts.append(context.log_summary)

        # Add raw log content (limited)
        if context.log_content:
            # Only use last 10KB of logs for classification
            parts.append(context.log_content.raw_content[-10000:])

        return "\n".join(parts)
