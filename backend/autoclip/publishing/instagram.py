"""Instagram Reels publishing adapter using the Meta Graph API."""

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

GRAPH_API_VERSION = "v19.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def classify_meta_error(status_code: int, response_text: str) -> tuple[str, bool]:
    """Classify Meta Graph API error into (error_code, retryable)."""
    text = response_text.lower()
    if status_code == 429 or "rate limit" in text or '"code": 4' in text or '"code": 17' in text or '"code": 613' in text:
        return "rate_limit", True
    if status_code == 401 or "oauthexception" in text or '"code": 190' in text or '"code": 102' in text or "token" in text:
        return "authentication_error", False
    if status_code == 403 or '"code": 200' in text or "permission" in text:
        return "permission_error", False
    if "media" in text or "aspect ratio" in text or "resolution" in text or "duration" in text:
        return "invalid_media", False
    if status_code in (500, 502, 503, 504):
        return "platform_error", True
    if "timeout" in text or "timed out" in text or "connection" in text:
        return "network_error", True
    return "platform_error", False


class InstagramPublisher(BasePublisher):
    platform_name = "instagram"

    def __init__(
        self,
        access_token: str | None = None,
        account_id: str | None = None,
        control_plane_url: str | None = None,
    ) -> None:
        vault_access = None
        vault_acc_id = None
        try:
            from ..security.vault import get_vault
            vault = get_vault()
            vault_access = (
                vault.retrieve_secret("meta_access_token")
                or vault.retrieve_secret("META_ACCESS_TOKEN")
                or vault.retrieve_secret("instagram_access_token")
                or vault.retrieve_secret("INSTAGRAM_ACCESS_TOKEN")
            )
            vault_acc_id = (
                vault.retrieve_secret("instagram_account_id")
                or vault.retrieve_secret("INSTAGRAM_ACCOUNT_ID")
                or vault.retrieve_secret("meta_account_id")
                or vault.retrieve_secret("META_ACCOUNT_ID")
            )
        except Exception:
            pass

        self.access_token = (
            vault_access
            or access_token
            or os.getenv("META_ACCESS_TOKEN")
            or os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
        ).strip()

        raw_acc_id = account_id if account_id not in ("Instagram Account", "default", "", None) else None
        self.account_id = (
            vault_acc_id
            or raw_acc_id
            or os.getenv("INSTAGRAM_ACCOUNT_ID")
            or os.getenv("META_ACCOUNT_ID", "")
            or "17841439457167561"
        ).strip()
        if self.account_id in ("Instagram Account", "default", ""):
            self.account_id = "17841439457167561"

        self.control_plane_url = (
            control_plane_url
            or os.getenv("CONTROL_PLANE_URL")
            or os.getenv("RENDER_EXTERNAL_URL", "")
        ).strip().rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.access_token and self.account_id)

    def resolve_public_media_url(
        self,
        clip_id: str | None = None,
        export_id: str | None = None,
        drive_link: str | None = None,
        auth_token: str | None = None,
    ) -> str | None:
        """Resolves a direct binary video URL appropriate for Meta Graph API.

        Meta requires a direct media stream URL (not an HTML preview page).
        Priority:
        1. Direct public media endpoint: /api/media/{clip_id}
        2. Render Control Plane streaming endpoint: /api/exports/{id}/stream
        3. Google Drive direct download URL derived from file ID if available.
        """
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

    async def publish(
        self,
        media_path: Path,
        metadata: PublishingMetadata,
        drive_link: str | None = None,
        dry_run: bool = False,
    ) -> PublishingResult:
        destination_id = metadata.destination or self.account_id or "default"

        if not self.is_configured():
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=False,
                status="failed",
                error="META_ACCESS_TOKEN or INSTAGRAM_ACCOUNT_ID not configured in server environment.",
                error_code="authentication_error",
                retryable=False,
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

        # Dry run or validation
        is_live_disabled = os.getenv("META_DRY_RUN", "").lower() in ("true", "1", "yes") or os.getenv("META_PUBLISH_LIVE", "true").lower() in ("false", "0", "no")
        actual_dry_run = dry_run or is_live_disabled
        if actual_dry_run:
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
                        destination_id=destination_id,
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
                    code, retryable = classify_meta_error(resp.status_code, resp.text)
                    return PublishingResult(
                        platform=self.platform_name,
                        destination_id=destination_id,
                        success=False,
                        status="failed",
                        error=f"Meta Graph API authentication failed ({resp.status_code}): {resp.text}",
                        error_code=code,
                        retryable=retryable,
                    )
            except Exception as exc:
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error=f"Meta Graph API connection failed: {exc}",
                    error_code="network_error",
                    retryable=True,
                )

        from ..media_guard import is_valid_mp4
        if not is_valid_mp4(media_path):
            log.error("Instagram Publisher: Media file %s is not a valid MP4 video. Rejecting upload.", media_path)
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=False,
                status="failed",
                error=f"Media file {media_path} is invalid or corrupted (not a valid MP4 video).",
                error_code="invalid_media",
                retryable=False,
            )

        if not public_url:
            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=False,
                status="failed",
                error="Instagram Reels requires a publicly accessible video URL. Ensure CONTROL_PLANE_URL is configured.",
                error_code="invalid_media",
                retryable=False,
            )

        from ..seo.sanitizer import sanitize_public_text
        clean_desc = sanitize_public_text(metadata.description or "", is_title=False)
        clean_title = sanitize_public_text(metadata.title or "", is_title=True)

        if clean_desc:
            if clean_title and clean_title.lower() in clean_desc.lower():
                base_caption = clean_desc
            elif clean_title:
                base_caption = f"{clean_title}\n\n{clean_desc}"
            else:
                base_caption = clean_desc
        else:
            base_caption = clean_title or "Key Insight & Lesson"

        if metadata.tags:
            existing_tags = set(re.findall(r"#\w+", base_caption.lower()))
            clean_tags = [
                f"#{t.lstrip('#')}"
                for t in metadata.tags
                if not any(b in t.lower() for b in ("alamr", "autoclip"))
                and f"#{t.lstrip('#').lower()}" not in existing_tags
            ]
            if clean_tags:
                base_caption = f"{base_caption}\n\n{' '.join(clean_tags)}"

        caption = base_caption[:2200].strip()

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: Create media container
            create_url = f"{GRAPH_API_BASE}/{self.account_id}/media"
            create_params = {
                "media_type": "REELS",
                "video_url": public_url,
                "caption": caption,
                "share_to_feed": "true",
                "access_token": self.access_token,
            }
            create_resp = await client.post(create_url, data=create_params)
            if create_resp.status_code != 200:
                meta_err_msg = ""
                meta_err_type = ""
                meta_err_code = None
                meta_err_subcode = None
                meta_trace_id = ""
                try:
                    meta_json = create_resp.json()
                    meta_err_obj = meta_json.get("error", {})
                    meta_err_msg = meta_err_obj.get("message") or ""
                    meta_err_type = meta_err_obj.get("type") or ""
                    meta_err_code = meta_err_obj.get("code")
                    meta_err_subcode = meta_err_obj.get("error_subcode")
                    meta_trace_id = meta_err_obj.get("fbtrace_id") or ""
                except Exception:
                    pass

                err_summary = meta_err_msg or create_resp.text or f"HTTP {create_resp.status_code}"
                err = f"Failed to create Instagram Reel container ({create_resp.status_code}): {err_summary}"
                log.error(
                    "Instagram container creation failed: status=%s type=%s code=%s subcode=%s trace=%s msg=%s",
                    create_resp.status_code,
                    meta_err_type,
                    meta_err_code,
                    meta_err_subcode,
                    meta_trace_id,
                    meta_err_msg,
                )
                code, retryable = classify_meta_error(create_resp.status_code, create_resp.text)
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error=err,
                    error_code=code,
                    retryable=retryable,
                    details={
                        "http_status": create_resp.status_code,
                        "error_type": meta_err_type,
                        "error_code": meta_err_code,
                        "error_subcode": meta_err_subcode,
                        "fbtrace_id": meta_trace_id,
                        "message": meta_err_msg,
                        "public_url": public_url,
                    },
                )

            container_id = create_resp.json().get("id")
            if not container_id:
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error=f"No container ID in response: {create_resp.text}",
                    error_code="platform_error",
                    retryable=True,
                )

            # Step 2: Poll container status (up to 150s for transcoding & ingestion)
            status_url = f"{GRAPH_API_BASE}/{container_id}"
            is_ready = False
            for attempt in range(30):
                await asyncio.sleep(5)
                stat_resp = await client.get(status_url, params={"fields": "status_code,status", "access_token": self.access_token})
                if stat_resp.status_code == 200:
                    stat_json = stat_resp.json()
                    status_code = stat_json.get("status_code")
                    if status_code == "FINISHED":
                        is_ready = True
                        break
                    elif status_code == "ERROR":
                        err_detail = stat_json.get("status") or stat_resp.text
                        err = f"Instagram media container processing error: {err_detail}"
                        is_transient = "2207082" in err_detail or "temporary" in err_detail.lower()
                        return PublishingResult(
                            platform=self.platform_name,
                            destination_id=destination_id,
                            success=False,
                            status="failed",
                            error=err,
                            error_code="platform_error" if is_transient else "invalid_media",
                            retryable=is_transient,
                        )
                if (attempt + 1) % 4 == 0:
                    log.info("Still waiting for Instagram media container %s (attempt %d/30)...", container_id, attempt + 1)

            if not is_ready:
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error="Timeout waiting for Instagram media container processing.",
                    error_code="network_error",
                    retryable=True,
                )

            # Step 3: Publish container
            publish_url = f"{GRAPH_API_BASE}/{self.account_id}/media_publish"
            pub_resp = await client.post(publish_url, data={"creation_id": container_id, "access_token": self.access_token})
            if pub_resp.status_code != 200:
                err = f"Failed to publish Instagram Reel ({pub_resp.status_code}): {pub_resp.text}"
                code, retryable = classify_meta_error(pub_resp.status_code, pub_resp.text)
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error=err,
                    error_code=code,
                    retryable=retryable,
                )

            media_id = pub_resp.json().get("id", container_id)

            # Retrieve permalink
            permalink = None
            try:
                perm_resp = await client.get(f"{GRAPH_API_BASE}/{media_id}", params={"fields": "permalink", "access_token": self.access_token})
                if perm_resp.status_code == 200:
                    permalink = perm_resp.json().get("permalink")
            except Exception:
                pass

            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()
            final_permalink = permalink or f"https://www.instagram.com/reel/{media_id}/"

            return PublishingResult(
                platform=self.platform_name,
                destination_id=destination_id,
                success=True,
                status="published",
                external_id=media_id,
                remote_media_id=media_id,
                remote_post_id=media_id,
                permalink=final_permalink,
                url=final_permalink,
                published_at=now_iso,
                details={"container_id": container_id, "media_id": media_id},
            )


async def validate_instagram_credentials(
    access_token: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Validate Meta Graph API credentials and confirm Instagram Business/Creator Account status."""
    tok = access_token
    acc = account_id
    if not tok or not acc:
        pub = InstagramPublisher(access_token=tok, account_id=acc)
        tok = tok or pub.access_token
        acc = acc or pub.account_id

    if not tok or not acc:
        return {
            "valid": False,
            "configured": False,
            "error": "Instagram credentials (INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_ACCOUNT_ID) are not configured.",
            "account_name": None,
            "account_id": None,
        }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # First try directly querying the account ID
            acc_url = f"{GRAPH_API_BASE}/{acc}"
            resp = await client.get(
                acc_url,
                params={"fields": "id,username,name", "access_token": tok},
            )
            if resp.status_code == 200:
                data = resp.json()
                username = data.get("username")
                name = data.get("name")
                display = f"@{username}" if username else (name or str(acc))
                return {
                    "valid": True,
                    "configured": True,
                    "error": None,
                    "account_name": display,
                    "account_id": acc,
                }

            # Fallback to /me validation
            me_resp = await client.get(
                f"{GRAPH_API_BASE}/me",
                params={"access_token": tok},
            )
            if me_resp.status_code == 200:
                me_data = me_resp.json()
                me_name = me_data.get("name", "Meta Account")
                return {
                    "valid": True,
                    "configured": True,
                    "error": None,
                    "account_name": f"{me_name} (ID: {acc})",
                    "account_id": acc,
                }
            else:
                code, _ = classify_meta_error(resp.status_code, resp.text)
                return {
                    "valid": False,
                    "configured": True,
                    "error": f"Meta Graph API error ({resp.status_code}): {resp.text}",
                    "account_name": None,
                    "account_id": acc,
                }
    except Exception as exc:
        return {
            "valid": False,
            "configured": True,
            "error": f"Meta Graph API connection error: {exc}",
            "account_name": None,
            "account_id": acc,
        }

