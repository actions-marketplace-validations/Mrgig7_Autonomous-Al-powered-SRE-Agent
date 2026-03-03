"""Response envelope helpers for Phase 1 onboarding APIs."""

from __future__ import annotations

from typing import Any

from fastapi import Request


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def use_response_envelope(request: Request) -> bool:
    """Determine whether caller requested the standardized API envelope."""
    query_value = request.query_params.get("envelope")
    if _is_truthy(query_value):
        return True

    header_value = request.headers.get("X-Response-Envelope")
    if _is_truthy(header_value):
        return True

    accept_header = request.headers.get("Accept", "")
    return "application/vnd.sre.enveloped+json" in accept_header


def success_response(data: Any) -> dict[str, Any]:
    """Create standardized success payload."""
    return {"success": True, "data": data, "error": None}


def error_response(message: str, code: str | None = None) -> dict[str, Any]:
    """Create standardized error payload."""
    return {
        "success": False,
        "data": None,
        "error": {"message": message, "code": code},
    }
