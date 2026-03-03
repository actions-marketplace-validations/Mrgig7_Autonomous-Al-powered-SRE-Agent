from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    countdown_seconds: int
    reason: str


class RetryablePipelineError(Exception):
    def __init__(self, *, countdown_seconds: int, reason: str):
        super().__init__(reason)
        self.countdown_seconds = countdown_seconds
        self.reason = reason


def compute_backoff_seconds(*, attempt: int, base: int, maximum: int) -> int:
    if attempt <= 1:
        return min(base, maximum)
    value = base * (2 ** (attempt - 1))
    return min(int(value), int(maximum))


def is_retryable_exception(exc: Exception) -> bool:
    try:
        from sqlalchemy.exc import OperationalError
    except Exception:
        operational_error: type[BaseException] | None = None
    else:
        operational_error = OperationalError

    retryable: tuple[type[BaseException], ...] = (
        TimeoutError,
        ConnectionError,
        OSError,
    )
    if operational_error and isinstance(exc, operational_error):
        return True
    if isinstance(exc, retryable):
        return True

    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
            return True
    except Exception:
        pass

    try:
        import redis

        if isinstance(exc, redis.exceptions.TimeoutError | redis.exceptions.ConnectionError):
            return True
    except Exception:
        pass

    return False
