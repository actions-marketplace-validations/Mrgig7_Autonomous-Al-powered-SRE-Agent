from __future__ import annotations

from sre_agent.explainability.redactor import get_redactor


def test_redactor_masks_policy_secret_patterns() -> None:
    redactor = get_redactor()
    raw = 'password="supersecret" and ghp_123456789012345678901234567890123456'
    out = redactor.redact_text(raw)
    assert "supersecret" not in out
    assert "ghp_" not in out
    assert "[REDACTED]" in out


def test_redactor_masks_tokens_in_urls_and_headers() -> None:
    redactor = get_redactor()
    raw = "GET https://example.com/callback?token=abcd1234 Authorization: Bearer xyz"
    out = redactor.redact_text(raw)
    assert "abcd1234" not in out
    assert "Bearer xyz" not in out
    assert "[REDACTED]" in out
