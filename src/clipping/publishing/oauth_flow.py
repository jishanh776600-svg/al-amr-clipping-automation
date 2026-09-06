"""Official YouTube Data API OAuth 2.0 Flow & Channel Enrollment Engine.

Provides secure browser consent link generation, code-for-token exchange,
non-destructive channel identity verification, and safe storage into EncryptedCredentialVault.
Strictly redacts tokens and credentials from logs and error output.
"""

import urllib.parse
from typing import Any, Dict, Optional
import httpx
from pydantic import SecretStr

from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.logging.logger import get_logger
from clipping.publishing.oauth import OAuthCredentials, OAuthTokenManager

logger = get_logger("clipping.publishing.oauth_flow")

GOOGLE_AUTH_BASE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
YOUTUBE_CHANNELS_ENDPOINT = "https://www.googleapis.com/youtube/v3/channels"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class YouTubeOAuthFlow:
    """Orchestrates Google OAuth 2.0 authorization for YouTube creator accounts."""

    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout = timeout_seconds

    @staticmethod
    def generate_authorization_url(
        client_id: str,
        redirect_uri: str = "http://localhost:8000/api/auth/youtube/callback",
        state: Optional[str] = None,
    ) -> str:
        """Constructs official Google OAuth2 authorization URL with offline consent."""
        params = {
            "client_id": client_id.strip(),
            "redirect_uri": redirect_uri.strip(),
            "response_type": "code",
            "scope": " ".join(YOUTUBE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }
        if state:
            params["state"] = state
        return f"{GOOGLE_AUTH_BASE}?{urllib.parse.urlencode(params)}"

    async def exchange_code_for_tokens(
        self,
        client_id: str,
        client_secret: str,
        authorization_code: str,
        redirect_uri: str = "http://localhost:8000/api/auth/youtube/callback",
    ) -> Dict[str, Any]:
        """
        Exchanges Google OAuth2 authorization code for access and refresh tokens.
        Never logs client secrets, authorization codes, or refresh tokens.
        """
        payload = {
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "code": authorization_code.strip(),
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri.strip(),
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=payload)
                if resp.status_code != 200:
                    logger.error("Google OAuth token exchange failed", status_code=resp.status_code)
                    raise RuntimeError(f"Google OAuth token exchange failed with HTTP {resp.status_code}")
                data = resp.json()
                if "refresh_token" not in data:
                    logger.warning("Google did not return refresh_token (consent might have been cached)")
                return data
            except httpx.HTTPError as e:
                logger.error("Network error during Google OAuth token exchange", error=str(e))
                raise RuntimeError("Google OAuth token exchange network error")

    async def verify_channel_identity(self, access_token: str) -> Dict[str, Any]:
        """
        Performs read-only verification of the authenticated Google/YouTube channel.
        Zero media is modified or uploaded.
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        params = {
            "part": "snippet,contentDetails",
            "mine": "true",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(YOUTUBE_CHANNELS_ENDPOINT, headers=headers, params=params)
                if resp.status_code != 200:
                    logger.error("YouTube Data API channel verification failed", status_code=resp.status_code)
                    raise RuntimeError(f"YouTube Data API channel verification failed with HTTP {resp.status_code}")

                data = resp.json()
                items = data.get("items", [])
                if not items:
                    raise ValueError("Authenticated Google account has no associated YouTube channel")

                item = items[0]
                channel_id = item.get("id", "Unknown")
                snippet = item.get("snippet", {})
                channel_title = snippet.get("title", "Unknown Channel")
                custom_url = snippet.get("customUrl")

                logger.info("Successfully verified YouTube channel identity", channel_id=channel_id, channel_title=channel_title)
                return {
                    "channel_id": channel_id,
                    "channel_title": channel_title,
                    "custom_url": custom_url,
                }
            except httpx.HTTPError as e:
                logger.error("Network error during YouTube channel verification", error=str(e))
                raise RuntimeError("YouTube channel verification network error")

    async def complete_enrollment(
        self,
        vault: EncryptedCredentialVault,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        access_token: Optional[str] = None,
    ) -> AccountMetadata:
        """
        Validates channel identity and securely registers the account into EncryptedCredentialVault.
        """
        # If access token not provided, refresh using refresh_token
        if not access_token:
            token_mgr = OAuthTokenManager(
                credentials=OAuthCredentials(
                    client_id=client_id,
                    client_secret=SecretStr(client_secret),
                    refresh_token=SecretStr(refresh_token),
                )
            )
            access_token = await token_mgr.get_access_token(force_refresh=True)

        channel_info = await self.verify_channel_identity(access_token)

        meta = AccountMetadata(
            platform=AccountPlatform.YOUTUBE,
            account_id=channel_info["channel_id"],
            username=channel_info["channel_title"],
            display_name=channel_info["channel_title"],
            status=AccountStatus.ACTIVE,
            reuse_eligibility=True,
            tags=["verified_oauth", "production_creator"],
        )

        sensitive_creds = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }

        await vault.save_account(meta, sensitive_credentials=sensitive_creds)
        logger.info("Creator account successfully enrolled in vault", account_id=meta.account_id, platform=meta.platform.value)
        return meta
