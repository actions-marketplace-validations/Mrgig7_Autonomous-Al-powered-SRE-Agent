from __future__ import annotations

from sre_agent.ops.retry_policy import compute_backoff_seconds, is_retryable_exception


def test_compute_backoff_seconds_exponential_and_capped() -> None:
    assert compute_backoff_seconds(attempt=1, base=30, maximum=600) == 30
    assert compute_backoff_seconds(attempt=2, base=30, maximum=600) == 60
    assert compute_backoff_seconds(attempt=3, base=30, maximum=600) == 120
    assert compute_backoff_seconds(attempt=10, base=30, maximum=600) == 600


def test_is_retryable_exception_covers_common_transients() -> None:
    assert is_retryable_exception(TimeoutError("t")) is True
    assert is_retryable_exception(ConnectionError("c")) is True
    assert is_retryable_exception(OSError("o")) is True
    assert is_retryable_exception(ValueError("v")) is False
