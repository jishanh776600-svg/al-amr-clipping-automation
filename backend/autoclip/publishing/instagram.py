"""Instagram Reels publishing adapter using the Meta Graph API."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any
import urllib.parse

import httpx

from .base import BasePublisher, PublishingMetadata, PublishingResult

log = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


class InstagramPublisher(BasePublisher):
    platform_name = "instagram"

    def __init__(
        self,
        access_token: str | None = None,
        account_id: str | None = None,
        control_plane_url: str | None = None,
    ) -> None:
        self.access_token = (
            access_token
            or os.getenv("META_ACCESS_TOKEN")
            or os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
        ).strip()
        self.account_id = (
            account_id
            or os.getenv("INSTAGRAM_ACCOUNT_ID")
            or os.getenv("META_ACCOUNT_ID", "")
        ).strip()
        self.control_plane_url = (
            control_plane_url
            or os.getenv("CONTROL_PLANE_URL")
            or os.getenv("RENDER_EXTERNAL_URL", "")
        ).strip().rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.access_token and self.account_id)

    def resolve_public_media_url(
        self,
        export_id: str | None,
        drive_link: str | None = None,
        auth_token: str | None = None,
    ) -> str | None:
        """Resolves a direct binary video URL appropriate for Meta Graph API.

        Meta requires a direct media stream URL (not an HTML preview page).
        Priority:
        1. Render Control Plane streaming endpoint: /api/exports/{id}/stream?token=...
        2. Google Drive direct download URL derived from file ID if available.
        """
        if self.control_plane_url and export_id:
            query = f"?token={auth_token}" if auth_token else ""
            return f"{self.control_plane_url}/api/exports/{export_id}/stream{query}"

        if drive_link and "drive.google.com/file/d/" in drive_link:
            # Extract file ID from https://drive.google.com/file/d/{file_id}/view...
            try:
                parts = drive_link.split("/file/d/")
                file_id = parts[1].split("/")[0].split("?")[0]
                return f"https://drive.google.com/uc?export=download&id={file_id}"
            except Exception:
                pass

        return None

    async def publish(
        self,
        media_path: Path,
        metadata: PublishingMetadata,
        drive_link: str | None = None,
        dry_run: bool = False,
    ) -> PublishingResult:
        if not self.is_configured():
            return PublishingResult(
                platform=self.platform_name,
                success=False,
                status="skipped",
                error="META_ACCESS_TOKEN or INSTAGRAM_ACCOUNT_ID not configured.",
            )

        export_id = metadata.extra.get("export_id")
        auth_token = metadata.extra.get("callback_token") or os.getenv("OPERATOR_TOKEN")
        public_url = self.resolve_public_media_url(export_id, drive_link, auth_token)

        # Dry run or validation
        if dry_run or not os.getenv("META_PUBLISH_LIVE", "").lower() in ("true", "1", "yes"):
            # Verify access token with Graph API
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        f"{GRAPH_API_BASE}/me",
                        params={"access_token": self.access_token},
                    )
                if resp.status_code == 200:
                    user_info = resp.json()
                    log.info("Instagram Publisher: Meta credentials verified for %s.", user_info.get("name", "Account"))
                    return PublishingResult(
                        platform=self.platform_name,
                        success=True,
                        status="ready_for_upload",
                        details={
                            "mode": "dry_run",
                            "account_id": self.account_id,
                            "user_info": user_info,
                            "public_url_available": bool(public_url),
                            "note": "Credentials verified. Set META_PUBLISH_LIVE=true to execute live publish.",
                        },
                    )
                else:
                    return PublishingResult(
                        platform=self.platform_name,
                        success=False,
                        status="failed",
                        error=f"Meta Graph API authentication failed ({resp.status_code}): {resp.text}",
                    )
            except Exception as exc:
                return PublishingResult(
                    platform=self.platform_name,
                    success=False,
                    status="failed",
                    error=f"Meta Graph API connection failed: {exc}",
                )

        if not public_url:
            return PublishingResult(
                platform=self.platform_name,
                success=False,
                status="failed",
                error="Instagram Reels requires a publicly accessible video URL. Ensure CONTROL_PLANE_URL is configured.",
            )

        caption = metadata.title
        if metadata.description:
            caption = f"{caption}\n\n{metadata.description}"
        if metadata.tags:
            caption = f"{caption}\n\n" + " ".join(f"#{t.lstrip('#')}" for t in metadata.tags)
        caption = caption[:2200]

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: Create media container
            create_url = f"{GRAPH_API_BASE}/{self.account_id}/media"
            create_params = {
                "media_type": "REELS",
                "video_url": public_url,
                "caption": caption,
                "access_token": self.access_token,
            }
            create_resp = await client.post(create_url, data=create_params)
            if create_resp.status_code != 200:
                err = f"Failed to create Instagram Reel container ({create_resp.status_code}): {create_resp.text}"
                log.error(err)
                return PublishingResult(platform=self.platform_name, success=False, status="failed", error=err)

            container_id = create_resp.json().get("id")
            if not container_id:
                return PublishingResult(
                    platform=self.platform_name,
                    success=False,
                    status="failed",
                    error=f"No container ID in response: {create_resp.text}",
                )

            # Step 2: Poll container status
            status_url = f"{GRAPH_API_BASE}/{container_id}"
            is_ready = False
            for _ in range(12):  # Poll up to 60s
                await asyncio.sleep(5)
                stat_resp = await client.get(status_url, params={"fields": "status_code", "access_token": self.access_token})
                if stat_resp.status_code == 200:
                    status_code = stat_resp.json().get("status_code")
                    if status_code == "FINISHED":
                        is_ready = True
                        break
                    elif status_code == "ERROR":
                        err = f"Instagram media container processing error: {stat_resp.text}"
                        return PublishingResult(platform=self.platform_name, success=False, status="failed", error=err)

            if not is_ready:
                return PublishingResult(
                    platform=self.platform_name,
                    success=False,
                    status="failed",
                    error="Timeout waiting for Instagram media container processing.",
                )

            # Step 3: Publish container
            publish_url = f"{GRAPH_API_BASE}/{self.account_id}/media_publish"
            pub_resp = await client.post(publish_url, data={"creation_id": container_id, "access_token": self.access_token})
            if pub_resp.status_code != 200:
                err = f"Failed to publish Instagram Reel ({pub_resp.status_code}): {pub_resp.text}"
                return PublishingResult(platform=self.platform_name, success=False, status="failed", error=err)

            media_id = pub_resp.json().get("id", container_id)

            # Retrieve permalink
            permalink = None
            perm_resp = await client.get(f"{GRAPH_API_BASE}/{media_id}", params={"fields": "permalink", "access_token": self.access_token})
            if perm_resp.status_code == 200:
                permalink = perm_resp.json().get("permalink")

            return PublishingResult(
                platform=self.platform_name,
                success=True,
                status="published",
                external_id=media_id,
                url=permalink or f"https://www.instagram.com/reel/{media_id}/",
                details={"container_id": container_id, "media_id": media_id},
            )
