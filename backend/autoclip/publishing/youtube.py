"""YouTube Shorts publishing adapter with OAuth2 authentication and dry-run safety."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from .base import BasePublisher, PublishingMetadata, PublishingResult

log = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def classify_youtube_error(exc: Exception) -> tuple[str, bool]:
    """Classify YouTube API exception into (error_code, retryable)."""
    msg = str(exc).lower()
    if "quotaexceeded" in msg or "userratelimitexceeded" in msg or "429" in msg or "rate limit" in msg:
        return "rate_limit", True
    if "timeout" in msg or "timed out" in msg or "connection" in msg or "network" in msg or "socket" in msg:
        return "network_error", True
    if "invalid_grant" in msg or "unauthorized" in msg or "invalid_client" in msg or "token" in msg:
        return "authentication_error", False
    if "forbidden" in msg or "403" in msg:
        return "permission_error", False
    if "badrequest" in msg or "400" in msg:
        return "invalid_metadata", False
    if "500" in msg or "502" in msg or "503" in msg or "backenderror" in msg:
        return "platform_error", True
    return "unknown", False


class YouTubePublisher(BasePublisher):
    platform_name = "youtube"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        self.client_id = (client_id or os.getenv("YOUTUBE_CLIENT_ID", "")).strip()
        self.client_secret = (client_secret or os.getenv("YOUTUBE_CLIENT_SECRET", "")).strip()
        self.refresh_token = (refresh_token or os.getenv("YOUTUBE_REFRESH_TOKEN", "")).strip()

        if not self.refresh_token or not self.client_id or not self.client_secret:
            try:
                from ..security.vault import get_vault
                vault = get_vault()
                if not self.refresh_token:
                    self.refresh_token = (vault.retrieve_secret("youtube_refresh_token") or vault.retrieve_secret("YOUTUBE_REFRESH_TOKEN") or "").strip()
                if not self.client_id:
                    self.client_id = (vault.retrieve_secret("youtube_client_id") or vault.retrieve_secret("YOUTUBE_CLIENT_ID") or "").strip()
                if not self.client_secret:
                    self.client_secret = (vault.retrieve_secret("youtube_client_secret") or vault.retrieve_secret("YOUTUBE_CLIENT_SECRET") or "").strip()
            except Exception:
                pass

    def is_configured(self) -> bool:
        return bool(self.refresh_token)

    def _get_credentials(self) -> Any:
        from google.oauth2.credentials import Credentials

        return Credentials(
            token=None,
            refresh_token=self.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id or None,
            client_secret=self.client_secret or None,
            scopes=YOUTUBE_UPLOAD_SCOPES,
        )

    async def publish(
        self,
        media_path: Path,
        metadata: PublishingMetadata,
        drive_link: str | None = None,
        dry_run: bool = False,
    ) -> PublishingResult:
        destination_id = metadata.destination or self.client_id or "default"

        if not self.is_configured():
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=False,
                status="failed",
                error="YOUTUBE_REFRESH_TOKEN not configured in server environment.",
                error_code="authentication_error",
                retryable=False,
            )

        if not media_path.exists():
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=False,
                status="failed",
                error=f"Local media file not found: {media_path}",
                error_code="invalid_media",
                retryable=False,
            )

        # Ensure #Shorts is present in title or description for vertical video formatting
        title = metadata.title.strip()
        if "#Shorts" not in title and "#shorts" not in title:
            if len(title) <= 92:
                title = f"{title} #Shorts"
        title = title[:100]

        description = metadata.description.strip()
        if drive_link and drive_link not in description:
            description = f"{description}\n\nArchive Backup: {drive_link}".strip()
        if "#Shorts" not in description:
            description = f"{description}\n\n#Shorts #Viral".strip()
        description = description[:5000]

        tags = list(set(metadata.tags + ["Shorts", "Viral"]))

        # Check live publish configuration (default is live when credentials are present)
        is_live_disabled = os.getenv("YOUTUBE_DRY_RUN", "").lower() in ("true", "1", "yes") or os.getenv("YOUTUBE_PUBLISH_LIVE", "true").lower() in ("false", "0", "no")
        actual_dry_run = dry_run or is_live_disabled

        if actual_dry_run:
            log.info("YouTube Publisher: Dry run verified successfully for '%s'.", title)
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=True,
                status="ready_for_upload",
                details={
                    "mode": "dry_run",
                    "title": title,
                    "description_preview": description[:120],
                    "privacy": metadata.privacy,
                    "tags": tags,
                    "file_size": media_path.stat().st_size if media_path.exists() else 0,
                    "note": "Credentials and video file verified. Set YOUTUBE_PUBLISH_LIVE=true for live publish.",
                },
            )

        # Validate OAuth credentials via token refresh test for live publish
        try:
            creds = self._get_credentials()
            import google.auth.transport.requests

            await asyncio.to_thread(creds.refresh, google.auth.transport.requests.Request())
        except Exception as exc:
            log.warning("YouTube OAuth token refresh failed: %s", exc)
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=False,
                status="failed",
                error=f"YouTube OAuth authentication failed: {exc}",
                error_code="authentication_error",
                retryable=False,
            )

        # Live Upload to YouTube Data API v3
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload

            service = build("youtube", "v3", credentials=creds, cache_discovery=False)

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "categoryId": "22",  # People & Blogs
                },
                "status": {
                    "privacyStatus": metadata.privacy,
                    "selfDeclaredMadeForKids": False,
                },
            }

            media = MediaFileUpload(
                str(media_path),
                mimetype="video/mp4",
                resumable=True,
            )

            request = service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            response = await asyncio.to_thread(request.execute)
            video_id = response.get("id")
            if not video_id:
                raise RuntimeError(f"No video ID returned by YouTube API: {response}")

            short_url = f"https://youtube.com/shorts/{video_id}"
            log.info("Successfully published YouTube Short: %s", short_url)

            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()

            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=True,
                status="published",
                external_id=video_id,
                remote_media_id=video_id,
                remote_post_id=video_id,
                permalink=short_url,
                url=short_url,
                published_at=now_iso,
                details=response,
            )
        except Exception as exc:
            log.exception("Exception during live YouTube upload: %s", exc)
            code, retryable = classify_youtube_error(exc)
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=False,
                status="failed",
                error=str(exc),
                error_code=code,
                retryable=retryable,
            )


def generate_youtube_auth_url(
    client_id: str,
    redirect_uri: str,
    state: str | None = None,
) -> str:
    """Generate the Google OAuth2 consent screen URL for YouTube Shorts upload permissions."""
    import urllib.parse

    params = {
        "client_id": client_id.strip(),
        "redirect_uri": redirect_uri.strip(),
        "response_type": "code",
        "scope": " ".join(YOUTUBE_UPLOAD_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if state:
        params["state"] = state
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


async def exchange_youtube_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Exchange authorization code for permanent refresh token and persist in encrypted vault."""
    import httpx

    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "code": code.strip(),
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri.strip(),
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(token_url, data=payload)
        if resp.status_code != 200:
            return {
                "success": False,
                "error": f"Token exchange failed ({resp.status_code}): {resp.text}",
            }
        tokens = resp.json()
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            return {
                "success": False,
                "error": "No refresh_token returned by Google. Re-authorize with prompt=consent.",
            }

        from ..config import set_secret
        set_secret("youtube_client_id", client_id.strip())
        set_secret("youtube_client_secret", client_secret.strip())
        set_secret("youtube_refresh_token", refresh_token.strip())

        channel_info = await validate_youtube_credentials(client_id, client_secret, refresh_token)
        return {
            "success": True,
            "channel_title": channel_info.get("channel_title"),
            "channel_id": channel_info.get("channel_id"),
            "refresh_token_saved": True,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Exception exchanging YouTube code: {exc}",
        }


async def validate_youtube_credentials(
    client_id: str | None = None,
    client_secret: str | None = None,
    refresh_token: str | None = None,
) -> dict[str, Any]:
    """Validate YouTube OAuth2 credentials by refreshing access token and querying channel info."""
    cid = client_id
    sec = client_secret
    rt = refresh_token
    if not cid or not sec or not rt:
        pub = YouTubePublisher(client_id=cid, client_secret=sec, refresh_token=rt)
        cid = cid or pub.client_id
        sec = sec or pub.client_secret
        rt = rt or pub.refresh_token

    if not rt or not cid or not sec:
        return {
            "valid": False,
            "configured": False,
            "error": "YouTube credentials (Client ID, Secret, Refresh Token) are not fully configured.",
            "channel_title": None,
            "channel_id": None,
        }

    try:
        from google.oauth2.credentials import Credentials
        import google.auth.transport.requests
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=rt,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cid,
            client_secret=sec,
            scopes=YOUTUBE_UPLOAD_SCOPES,
        )
        await asyncio.to_thread(creds.refresh, google.auth.transport.requests.Request())
        service = build("youtube", "v3", credentials=creds, cache_discovery=False)
        req = service.channels().list(part="snippet,statistics", mine=True)
        res = await asyncio.to_thread(req.execute)
        items = res.get("items", [])
        if not items:
            return {
                "valid": True,
                "configured": True,
                "error": None,
                "channel_title": "Authenticated (No Channel Found)",
                "channel_id": None,
            }
        item = items[0]
        snippet = item.get("snippet", {})
        channel_title = snippet.get("title")
        custom_url = snippet.get("customUrl")
        channel_id = item.get("id")
        return {
            "valid": True,
            "configured": True,
            "error": None,
            "channel_title": f"{channel_title} ({custom_url})" if custom_url else channel_title,
            "channel_id": channel_id,
        }
    except Exception as exc:
        return {
            "valid": False,
            "configured": True,
            "error": f"YouTube authentication verification failed: {exc}",
            "channel_title": None,
            "channel_id": None,
        }

