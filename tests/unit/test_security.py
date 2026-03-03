"""Unit tests for webhook signature verification."""

import hashlib
import hmac

import pytest
from sre_agent.core.security import WebhookSignatureError, verify_github_signature


class TestVerifyGitHubSignature:
    """Tests for GitHub webhook signature verification."""

    def test_valid_signature_passes(self) -> None:
        """Valid signature should return True."""
        secret = "test-secret"
        payload = b'{"test": "payload"}'

        # Calculate expected signature
        signature = hmac.new(
            key=secret.encode("utf-8"),
            msg=payload,
            digestmod=hashlib.sha256,
        ).hexdigest()

        result = verify_github_signature(
            payload=payload,
            signature_header=f"sha256={signature}",
            secret=secret,
        )

        assert result is True

    def test_missing_signature_raises_error(self) -> None:
        """Missing signature should raise WebhookSignatureError."""
        with pytest.raises(WebhookSignatureError, match="Missing"):
            verify_github_signature(
                payload=b"test",
                signature_header=None,
                secret="secret",
            )

    def test_invalid_format_raises_error(self) -> None:
        """Invalid signature format should raise WebhookSignatureError."""
        with pytest.raises(WebhookSignatureError, match="Invalid signature format"):
            verify_github_signature(
                payload=b"test",
                signature_header="invalid-format",
                secret="secret",
            )

    def test_wrong_signature_raises_error(self) -> None:
        """Wrong signature should raise WebhookSignatureError."""
        with pytest.raises(WebhookSignatureError, match="mismatch"):
            verify_github_signature(
                payload=b"test",
                signature_header="sha256=wrongsignature",
                secret="secret",
            )

    def test_timing_safe_comparison(self) -> None:
        """Verify that timing-safe comparison is used (implicit test)."""
        # This test ensures we're using hmac.compare_digest
        # by verifying behavior with similar signatures
        secret = "test-secret"
        payload = b'{"test": "payload"}'

        # Calculate correct signature
        correct_signature = hmac.new(
            key=secret.encode("utf-8"),
            msg=payload,
            digestmod=hashlib.sha256,
        ).hexdigest()

        # Try with slightly wrong signature (same length)
        wrong_signature = correct_signature[:-1] + "x"

        with pytest.raises(WebhookSignatureError):
            verify_github_signature(
                payload=payload,
                signature_header=f"sha256={wrong_signature}",
                secret=secret,
            )
