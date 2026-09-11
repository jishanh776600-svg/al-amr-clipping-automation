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


def get_valid_api_keys() -> list[str]:
    """Return all configured server secrets."""
    keys: list[str] = []
    for var in (ENV_API_KEY, ENV_OPERATOR_TOKEN, "AL_AMR_MASTER_KEY", "WORKER_CALLBACK_SECRET"):
        val = os.environ.get(var)
        if val and val.strip():
            keys.append(val.strip())
    return keys


def get_configured_api_key() -> str | None:
    """Return the primary configured server secret, or None if unauthenticated."""
    keys = get_valid_api_keys()
    return keys[0] if keys else None


def is_valid_token(token: str | None) -> bool:
    """Validate token against all configured server secrets."""
    keys = get_valid_api_keys()
    if not keys:
        return True  # Permissive mode
    if not token:
        return False
    return any(hmac.compare_digest(token, k) for k in keys)


async def require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> None:
    """FastAPI dependency to guard endpoints."""
    keys = get_valid_api_keys()
    if not keys:
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
        # Fallback to X-API-Key header or query parameter 'token'
        token = request.headers.get("X-API-Key") or request.query_params.get("token")

    if not is_valid_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized. A valid Bearer token or X-API-Key is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

