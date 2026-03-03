"""Security utilities for webhook signature verification.

GitHub uses HMAC-SHA256 for webhook signature verification.
Reference: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
"""

import hashlib
import hmac
import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from sre_agent.config import Settings, get_settings

logger = logging.getLogger(__name__)


class WebhookSignatureError(Exception):
    """Raised when webhook signature verification fails."""

    pass


def verify_github_signature(
    payload: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """
    Verify GitHub webhook signature using HMAC-SHA256.

    Args:
        payload: Raw request body bytes
        signature_header: X-Hub-Signature-256 header value
        secret: Webhook secret configured in GitHub

    Returns:
        True if signature is valid

    Raises:
        WebhookSignatureError: If signature is missing, malformed, or invalid
    """
    if not signature_header:
        raise WebhookSignatureError("Missing X-Hub-Signature-256 header")

    if not signature_header.startswith("sha256="):
        raise WebhookSignatureError("Invalid signature format")

    # Extract the signature from header
    expected_signature = signature_header[7:]  # Remove "sha256=" prefix

    # Calculate HMAC-SHA256 of the payload
    computed_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Use timing-safe comparison to prevent timing attacks
    if not hmac.compare_digest(expected_signature, computed_signature):
        raise WebhookSignatureError("Signature mismatch")

    return True


async def get_verified_github_payload(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> tuple[bytes, str, str]:
    """
    FastAPI dependency for verified GitHub webhook payloads.

    Reads the raw request body, verifies the signature, and returns
    the payload along with event type and delivery ID.

    Args:
        request: FastAPI request object
        x_hub_signature_256: GitHub signature header
        x_github_event: GitHub event type header
        x_github_delivery: GitHub delivery ID header
        settings: Application settings

    Returns:
        Tuple of (raw_payload, event_type, delivery_id)

    Raises:
        HTTPException 401: If signature verification fails
        HTTPException 400: If required headers are missing
    """
    # Validate required headers
    if not x_github_event:
        logger.warning("Missing X-GitHub-Event header")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Event header",
        )

    if not x_github_delivery:
        logger.warning("Missing X-GitHub-Delivery header")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Delivery header",
        )

    # Read raw body
    body = await request.body()

    # Skip signature verification in dev mode if secret is not configured
    if not settings.github_webhook_secret:
        if settings.is_production:
            logger.error("CRITICAL: Webhook secret not configured in production")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server configuration error",
            )
        logger.warning("Webhook signature verification skipped (no secret configured)")
        return body, x_github_event, x_github_delivery

    # Verify signature
    try:
        verify_github_signature(
            payload=body,
            signature_header=x_hub_signature_256,
            secret=settings.github_webhook_secret,
        )
        logger.debug(
            "Webhook signature verified",
            extra={"delivery_id": x_github_delivery, "event": x_github_event},
        )
    except WebhookSignatureError as e:
        logger.warning(
            "Webhook signature verification failed",
            extra={
                "delivery_id": x_github_delivery,
                "event": x_github_event,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Signature verification failed: {e}",
        )

    return body, x_github_event, x_github_delivery
