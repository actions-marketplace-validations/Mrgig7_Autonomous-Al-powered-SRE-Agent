"""Structured logging configuration with JSON output and correlation IDs.

Uses python-json-logger for structured JSON logging suitable for
log aggregation systems like Loki, ELK, or CloudWatch.
"""

import logging
import sys
from contextvars import ContextVar
from typing import Any

from pythonjsonlogger import jsonlogger

from sre_agent.config import get_settings

# Context variable for correlation ID (request-scoped)
correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)
delivery_id_ctx: ContextVar[str | None] = ContextVar("delivery_id", default=None)
run_id_ctx: ContextVar[str | None] = ContextVar("run_id", default=None)
run_key_ctx: ContextVar[str | None] = ContextVar("run_key", default=None)
failure_id_ctx: ContextVar[str | None] = ContextVar("failure_id", default=None)


class CorrelationIdFilter(logging.Filter):
    """Log filter that adds correlation_id to all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_ctx.get()
        record.delivery_id = delivery_id_ctx.get() or record.correlation_id
        record.run_id = run_id_ctx.get()
        record.run_key = run_key_ctx.get()
        record.failure_id = failure_id_ctx.get()
        try:
            from sre_agent.observability.tracing import get_trace_ids

            trace_id, span_id = get_trace_ids()
            record.trace_id = trace_id
            record.span_id = span_id
        except Exception:
            record.trace_id = None
            record.span_id = None
        return True


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with additional fields."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        # Add standard fields
        log_record["timestamp"] = self.formatTime(record)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name

        # Add correlation ID if present
        if hasattr(record, "correlation_id") and record.correlation_id:
            log_record["correlation_id"] = record.correlation_id
        if hasattr(record, "delivery_id") and record.delivery_id:
            log_record["delivery_id"] = record.delivery_id
        if hasattr(record, "run_id") and record.run_id:
            log_record["run_id"] = record.run_id
        if hasattr(record, "run_key") and record.run_key:
            log_record["run_key"] = record.run_key
        if hasattr(record, "failure_id") and record.failure_id:
            log_record["failure_id"] = record.failure_id
        if hasattr(record, "trace_id") and record.trace_id:
            log_record["trace_id"] = record.trace_id
        if hasattr(record, "span_id") and record.span_id:
            log_record["span_id"] = record.span_id

        # Add source location for debugging
        log_record["module"] = record.module
        log_record["function"] = record.funcName
        log_record["line"] = record.lineno


def setup_logging() -> None:
    """Configure structured logging for the application."""
    settings = get_settings()

    # Create handler
    handler = logging.StreamHandler(sys.stdout)

    # Use JSON format in production, text format in dev
    if settings.log_format == "json":
        formatter = CustomJsonFormatter(
            fmt="%(timestamp)s %(level)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)

    # Add correlation ID filter
    handler.addFilter(CorrelationIdFilter())

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level)

    # Reduce verbosity of third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Log startup message
    logging.info(
        "Logging configured",
        extra={
            "environment": settings.environment,
            "log_level": settings.log_level,
            "log_format": settings.log_format,
        },
    )


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    return logging.getLogger(name)
