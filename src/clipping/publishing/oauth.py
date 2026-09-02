"""Secure OAuth2 Access Token Management for Unattended YouTube API Calls."""

import time
from typing import Optional
import httpx
from pydantic import BaseModel, SecretStr, ConfigDict
from clipping.logging.logger import get_logger

logger = get_logger("clipping.publishing.oauth")


class OAuthCredentials(BaseModel):
    """Holds OAuth2 client credentials and long-lived refresh token."""
    model_config = ConfigDict(frozen=True)

    client_id: str
    client_secret: SecretStr
    refresh_token: SecretStr


class OAuthTokenManager:
    """
    Manages short-lived Google OAuth2 access tokens for YouTube API publishing.
    Automatically refreshes expired tokens using the stored refresh token.
    Ensures credentials and tokens are never leaked into logs.
    """

    def __init__(
        self,
        credentials: OAuthCredentials,
        token_endpoint: str = "https://oauth2.googleapis.com/token",
    ):
        self.credentials = credentials
        self.token_endpoint = token_endpoint
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0

    async def get_access_token(self, force_refresh: bool = False) -> str:
        """
        Retrieves a valid access token, automatically refreshing if expired or forced.
        Applies a 60-second safety window before actual expiration.
        """
        now = time.time()
        if not force_refresh and self._access_token and (self._expires_at - now > 60.0):
            return self._access_token

        logger.info("Refreshing Google OAuth2 access token for YouTube publishing")
        payload = {
            "client_id": self.credentials.client_id,
            "client_secret": self.credentials.client_secret.get_secret_value(),
            "refresh_token": self.credentials.refresh_token.get_secret_value(),
            "grant_type": "refresh_token",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(self.token_endpoint, data=payload)
                resp.raise_for_status()
                data = resp.json()
                new_token = data.get("access_token")
                expires_in = int(data.get("expires_in", 3600))

                if not new_token:
                    raise ValueError("OAuth token response missing access_token")

                self._access_token = new_token
                self._expires_at = now + expires_in
                logger.info("Successfully refreshed OAuth2 access token", expires_in_sec=expires_in)
                return self._access_token

            except httpx.HTTPStatusError as e:
                # Mask secrets in status error
                logger.error("OAuth token refresh failed with HTTP status", status_code=e.response.status_code)
                raise RuntimeError(f"OAuth token refresh HTTP error: {e.response.status_code}")
            except Exception as e:
                logger.error("OAuth token refresh failed with network error", error=str(e))
                raise RuntimeError("OAuth token refresh failed")
