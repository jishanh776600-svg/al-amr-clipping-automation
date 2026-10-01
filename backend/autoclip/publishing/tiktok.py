"""TikTok Content Posting API v2 publishing adapter for AutoClip."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import re
from typing import Any
import urllib.parse

import httpx

from .base import BasePublisher, PublishingMetadata, PublishingResult

log = logging.getLogger(__name__)

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"


def classify_tiktok_error(status_code: int, error_code: str, response_text: str) -> tuple[str, bool]:
    """Classify TikTok API response into (error_code, retryable)."""
    text = (response_text or "").lower()
    err_code = (error_code or "").lower()

    if status_code == 429 or "rate_limit" in err_code or "rate limit" in text or "too many requests" in text:
        return "rate_limit", True

    if (
        status_code == 401
        or "access_token_invalid" in err_code
        or "token_expired" in err_code
        or "token" in text
        or "unauthorized" in text
    ):
        return "authentication_error", False

    if status_code == 403 or "scope_not_authorized" in err_code or "permission" in text or "forbidden" in text:
        return "permission_error", False

    if (
        "video_format_invalid" in err_code
        or "video_size_limit_exceeded" in err_code
        or "video_duration_limit_exceeded" in err_code
        or "format" in text
        or "resolution" in text
        or "aspect ratio" in text
    ):
        return "invalid_media", False

    if status_code in (500, 502, 503, 504) or "server_error" in err_code:
        return "platform_error", True

    if "timeout" in text or "timed out" in text or "connection" in text:
        return "network_error", True

    return "platform_error", False


class TikTokPublisher(BasePublisher):
    """TikTok Video publisher adapter using TikTok Content Posting API v2."""

    platform_name = "tiktok"

    def __init__(
        self,
        access_token: str | None = None,
        client_key: str | None = None,
        client_secret: str | None = None,
        control_plane_url: str | None = None,
    ) -> None:
        vault_access = None
        vault_key = None
        vault_secret = None
        try:
            from ..security.vault import get_vault
            vault = get_vault()
            vault_access = (
                vault.retrieve_secret("tiktok_access_token")
                or vault.retrieve_secret("TIKTOK_ACCESS_TOKEN")
            )
            vault_key = (
                vault.retrieve_secret("tiktok_client_key")
                or vault.retrieve_secret("TIKTOK_CLIENT_KEY")
            )
            vault_secret = (
                vault.retrieve_secret("tiktok_client_secret")
                or vault.retrieve_secret("TIKTOK_CLIENT_SECRET")
            )
        except Exception:
            pass

        self.access_token = (
            vault_access
            or access_token
            or os.getenv("TIKTOK_ACCESS_TOKEN", "")
        ).strip()

        self.client_key = (
            vault_key
            or client_key
            or os.getenv("TIKTOK_CLIENT_KEY", "")
        ).strip()

        self.client_secret = (
            vault_secret
            or client_secret
            or os.getenv("TIKTOK_CLIENT_SECRET", "")
        ).strip()

        self.control_plane_url = (
            control_plane_url
            or os.getenv("CONTROL_PLANE_URL")
            or os.getenv("RENDER_EXTERNAL_URL", "")
        ).strip().rstrip("/")

    def is_configured(self) -> bool:
        """Returns True if required TikTok access token is present."""
        return bool(self.access_token)

    def resolve_public_media_url(
        self,
        clip_id: str | None = None,
        export_id: str | None = None,
        drive_link: str | None = None,
        auth_token: str | None = None,
    ) -> str | None:
        """Resolves a direct binary video URL accessible to TikTok servers."""
        if self.control_plane_url and clip_id:
            return f"{self.control_plane_url}/api/media/{clip_id}"

        if self.control_plane_url and export_id:
            query = f"?token={urllib.parse.quote(auth_token)}" if auth_token else ""
            return f"{self.control_plane_url}/api/exports/{export_id}/stream{query}"

        if drive_link and "drive.google.com/file/d/" in drive_link:
            try:
                parts = drive_link.split("/file/d/")
                file_id = parts[1].split("/")[0].split("?")[0]
                return f"https://drive.google.com/uc?export=download&id={file_id}"
            except Exception:
                pass

        return None

    def format_caption(self, metadata: PublishingMetadata) -> str:
        """Format caption with tags and mentions, capped at 2200 characters."""
        title = metadata.title.strip()
        tags = [f"#{t.lstrip('#')}" for t in metadata.tags if t.strip()]
        tags_str = " ".join(tags)

        desc = metadata.description.strip()
        parts = [title]
        if desc and desc != title:
            parts.append(desc)
        if tags_str:
            parts.append(tags_str)

        caption = "\n\n".join(parts)
        if len(caption) > 2200:
            caption = caption[:2195] + "..."
        return caption

    async def publish(
        self,
        media_path: Path,
        metadata: PublishingMetadata,
        drive_link: str | None = None,
        dry_run: bool = False,
    ) -> PublishingResult:
        destination_id = metadata.destination or "dest-tiktok-main"

        if not self.is_configured():
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=False,
                status="failed",
                error="TIKTOK_ACCESS_TOKEN not configured in server environment or vault.",
                error_code="authentication_error",
                retryable=False,
            )

        # 1. Media integrity validation
        from ..media_guard import is_valid_mp4
        if not is_valid_mp4(media_path):
            log.error(
                "TikTok publication rejected for %s: Media path '%s' is not a valid MP4 file.",
                metadata.title,
                media_path,
            )
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=False,
                status="failed",
                error="Media file is missing, empty, or not a valid MP4 video.",
                error_code="invalid_media",
                retryable=False,
            )

        file_size = media_path.stat().st_size
        caption = self.format_caption(metadata)

        # Map privacy
        privacy = "PUBLIC_TO_EVERYONE"
        if metadata.privacy == "private":
            privacy = "SELF_ONLY"
        elif metadata.privacy == "unlisted":
            privacy = "MUTUAL_FOLLOW_FRIENDS"

        # Check dry run
        is_live_disabled = (
            os.getenv("TIKTOK_DRY_RUN", "").lower() in ("true", "1", "yes")
            or os.getenv("TIKTOK_PUBLISH_LIVE", "true").lower() in ("false", "0", "no")
        )
        actual_dry_run = dry_run or is_live_disabled
        if actual_dry_run:
            log.info("TikTokPublisher: Dry run enabled. Skipping live API call for '%s'.", metadata.title)
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=True,
                status="ready_for_upload",
                external_id=f"dry_run_{int(media_path.stat().st_mtime)}",
                url=f"https://www.tiktok.com/@preview/video/dry_run_{int(media_path.stat().st_mtime)}",
                details={
                    "dry_run": True,
                    "caption": caption,
                    "privacy": privacy,
                    "file_size": file_size,
                },
            )

        clip_id = metadata.extra.get("clip_id")
        export_id = metadata.extra.get("export_id")
        auth_token = metadata.extra.get("callback_token") or os.getenv("OPERATOR_TOKEN")
        public_url = self.resolve_public_media_url(
            clip_id=clip_id,
            export_id=export_id,
            drive_link=drive_link,
            auth_token=auth_token,
        )

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            # 2. Initialize video upload
            init_url = f"{TIKTOK_API_BASE}/post/publish/video/init/"
            post_info: dict[str, Any] = {
                "title": caption,
                "privacy_level": privacy,
                "disable_duet": metadata.extra.get("disable_duet", False),
                "disable_stitch": metadata.extra.get("disable_stitch", False),
                "disable_comment": metadata.extra.get("disable_comment", False),
                "video_cover_timestamp_ms": metadata.extra.get("video_cover_timestamp_ms", 1000),
            }

            if public_url:
                payload = {
                    "post_info": post_info,
                    "source_info": {
                        "source": "PULL_FROM_URL",
                        "video_url": public_url,
                    },
                }
            else:
                payload = {
                    "post_info": post_info,
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": file_size,
                        "chunk_size": file_size,
                        "total_chunk_count": 1,
                    },
                }

            try:
                init_resp = await client.post(init_url, headers=headers, json=payload)
            except Exception as e:
                log.exception("TikTok video init request failed: %s", e)
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error=f"TikTok connection error: {str(e)}",
                    error_code="network_error",
                    retryable=True,
                )

            if init_resp.status_code != 200:
                err_data = {}
                try:
                    err_data = init_resp.json()
                except Exception:
                    pass
                err_code = err_data.get("error", {}).get("code", "")
                err_msg = err_data.get("error", {}).get("message", init_resp.text)
                code, retryable = classify_tiktok_error(init_resp.status_code, err_code, err_msg)
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error=f"TikTok Init Error ({init_resp.status_code}): {err_msg}",
                    error_code=code,
                    retryable=retryable,
                )

            init_data = init_resp.json().get("data", {})
            publish_id = init_data.get("publish_id")
            upload_url = init_data.get("upload_url")

            # 3. Direct binary chunk upload if using FILE_UPLOAD
            if not public_url and upload_url:
                try:
                    with open(media_path, "rb") as vf:
                        chunk_data = vf.read()
                    up_headers = {
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                    }
                    up_resp = await client.put(upload_url, headers=up_headers, content=chunk_data)
                    if up_resp.status_code not in (200, 201, 204):
                        return PublishingResult(
                            platform=self.platform_name,
                            destination_id=destination_id,
                            success=False,
                            status="failed",
                            error=f"TikTok upload chunk failed with status {up_resp.status_code}: {up_resp.text}",
                            error_code="platform_error",
                            retryable=True,
                        )
                except Exception as e:
                    log.exception("TikTok binary upload failed: %s", e)
                    return PublishingResult(
                        platform=self.platform_name,
                        destination_id=destination_id,
                        success=False,
                        status="failed",
                        error=f"TikTok binary upload failed: {str(e)}",
                        error_code="network_error",
                        retryable=True,
                    )

            if not publish_id:
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error="TikTok init response did not return a publish_id.",
                    error_code="platform_error",
                    retryable=False,
                )

            # 4. Poll publish status
            status_url = f"{TIKTOK_API_BASE}/post/publish/status/fetch/"
            poll_headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            }
            final_status = "PROCESSING"
            fail_reason = ""

            for _ in range(8):
                await asyncio.sleep(3.0)
                try:
                    st_resp = await client.post(status_url, headers=poll_headers, json={"publish_id": publish_id})
                    if st_resp.status_code == 200:
                        st_data = st_resp.json().get("data", {})
                        st_val = st_data.get("status")
                        if st_val:
                            final_status = st_val
                            fail_reason = st_data.get("fail_reason", "")
                            if final_status in ("SUCCESS", "FAILED"):
                                break
                except Exception:
                    pass

            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()

            if final_status == "SUCCESS":
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=True,
                    status="published",
                    external_id=publish_id,
                    remote_post_id=publish_id,
                    published_at=now_iso,
                    details={"publish_id": publish_id, "status": "SUCCESS"},
                )

            if final_status in ("PROCESSING_DOWNLOAD", "PROCESSING_UPLOAD", "PROCESSING"):
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=True,
                    status="processing",
                    external_id=publish_id,
                    remote_post_id=publish_id,
                    error_code="processing_incomplete",
                    published_at=now_iso,
                    details={"publish_id": publish_id, "status": final_status},
                )

            # Failed
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=False,
                status="failed",
                external_id=publish_id,
                error=f"TikTok publication processing failed: {fail_reason or final_status}",
                error_code="processing_failed",
                retryable=False,
            )


async def validate_tiktok_credentials(access_token: str | None = None) -> dict[str, Any]:
    """Validate TikTok API access token and retrieve creator profile."""
    tok = access_token
    if not tok:
        pub = TikTokPublisher()
        tok = pub.access_token

    if not tok:
        return {
            "valid": False,
            "configured": False,
            "error": "TIKTOK_ACCESS_TOKEN is not configured.",
            "username": None,
            "display_name": None,
        }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            user_url = f"{TIKTOK_API_BASE}/user/info/"
            headers = {"Authorization": f"Bearer {tok.strip()}"}
            params = {"fields": "open_id,union_id,avatar_url,display_name,username"}
            resp = await client.get(user_url, headers=headers, params=params)

            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("user", {})
                username = data.get("username")
                display = data.get("display_name")
                return {
                    "valid": True,
                    "configured": True,
                    "username": username,
                    "display_name": display or f"@{username}",
                    "avatar_url": data.get("avatar_url"),
                    "open_id": data.get("open_id"),
                }

            err_data = {}
            try:
                err_data = resp.json()
            except Exception:
                pass
            msg = err_data.get("error", {}).get("message", resp.text)
            return {
                "valid": False,
                "configured": True,
                "error": f"TikTok API error ({resp.status_code}): {msg}",
                "username": None,
                "display_name": None,
            }
    except Exception as e:
        return {
            "valid": False,
            "configured": True,
            "error": f"Failed to connect to TikTok API: {str(e)}",
            "username": None,
            "display_name": None,
        }
