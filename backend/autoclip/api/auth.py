"""Authentication boundary for AutoClip production deployment.

Provides optional, lightweight Bearer token and API key validation.
If AUTOCLIP_API_KEY or OPERATOR_TOKEN is set in the environment, all private API
endpoints require authentication. If neither is set, the API operates in
permissive development mode (100% backwards compatible).
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)

ENV_API_KEY = "AUTOCLIP_API_KEY"
ENV_OPERATOR_TOKEN = "OPERATOR_TOKEN"

# Public paths that never require authentication (health probes, docs, static assets)
PUBLIC_PREFIXES = (
    "/health",
    "/ready",
    "/api/health",
    "/api/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/assets",
)


def get_configured_api_key() -> str | None:
    """Return the configured server secret, or None if unauthenticated."""
    return os.environ.get(ENV_API_KEY) or os.environ.get(ENV_OPERATOR_TOKEN) or None


async def require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> None:
    """FastAPI dependency to guard endpoints."""
    configured_key = get_configured_api_key()
    if not configured_key:
        return  # Permissive mode: no authentication required

    # Allow public endpoints
    path = request.url.path
    if any(path == p or path.startswith(f"{p}/") for p in PUBLIC_PREFIXES):
        return

    # Check Authorization: Bearer <token>
    token = None
    if credentials and credentials.credentials:
        token = credentials.credentials
    else:
        # Fallback to X-API-Key header
        token = request.headers.get("X-API-Key")

    if not token or not hmac.compare_digest(token, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized. A valid Bearer token or X-API-Key is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
