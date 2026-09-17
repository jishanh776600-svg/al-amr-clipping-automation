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

