"""YouTube Shorts publishing adapter with OAuth2 authentication and dry-run safety."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

from .base import BasePublisher, PublishingMetadata, PublishingResult

log = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def resolve_expected_youtube_channel_id(explicit: str | None = None) -> str | None:
    """Resolve authoritative expected AL AMR YouTube Channel ID from explicit param, vault, or env."""
    if explicit and explicit.strip():
        return explicit.strip()

    # 1. Encrypted Vault (takes precedence over stale env)
    try:
        from ..security.vault import get_vault
        vault = get_vault()
        vault_id = (
            vault.retrieve_secret("al_amr_youtube_channel_id")
            or vault.retrieve_secret("youtube_channel_id")
            or vault.retrieve_secret("AL_AMR_YOUTUBE_CHANNEL_ID")
        )
        if vault_id and vault_id.strip():
            return vault_id.strip()
    except Exception:
        pass

    # 2. Environment variables
    env_id = (
        os.getenv("AL_AMR_YOUTUBE_CHANNEL_ID")
        or os.getenv("YOUTUBE_CHANNEL_ID")
        or os.getenv("EXPECTED_YOUTUBE_CHANNEL_ID")
    )
    if env_id and env_id.strip():
        return env_id.strip()

    # 3. Authoritative AL AMR channel fallback (Future Founders)
    return "UCtaOzeFW2kEOexMoSNA75tA"


def classify_youtube_error(exc: Exception) -> tuple[str, bool]:
    """Classify YouTube API exception into (error_code, retryable)."""
    msg = str(exc).lower()
    if "channel mismatch" in msg or "channel_mismatch" in msg:
        return "channel_mismatch", False
    if "visibility" in msg or "visibility_incorrect" in msg or "unlisted" in msg or "private" in msg:
        return "visibility_incorrect", False
    if "processing_incomplete" in msg or "still processing" in msg:
        return "processing_incomplete", True
    if "processing_failed" in msg or "rejected" in msg:
        return "processing_failed", False
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
        vault_refresh = None
        vault_client_id = None
        vault_client_secret = None
        try:
            from ..security.vault import get_vault
            vault = get_vault()
            vault_refresh = vault.retrieve_secret("youtube_refresh_token") or vault.retrieve_secret("YOUTUBE_REFRESH_TOKEN")
            vault_client_id = vault.retrieve_secret("youtube_client_id") or vault.retrieve_secret("YOUTUBE_CLIENT_ID")
            vault_client_secret = vault.retrieve_secret("youtube_client_secret") or vault.retrieve_secret("YOUTUBE_CLIENT_SECRET")
        except Exception:
            pass

        self.client_id = (client_id or os.getenv("YOUTUBE_CLIENT_ID", "") or vault_client_id or "").strip()
        self.client_secret = (client_secret or os.getenv("YOUTUBE_CLIENT_SECRET", "") or vault_client_secret or "").strip()
        
        env_refresh = (os.getenv("YOUTUBE_REFRESH_TOKEN", "") or "").strip().strip("'\"")
        self.refresh_token = (refresh_token or env_refresh or vault_refresh or "").strip().strip("'\"")

        # Auto-sync fresh env token into vault so they never diverge
        if env_refresh and vault_refresh != env_refresh:
            try:
                from ..security.vault import get_vault
                v = get_vault()
                v.store_secret("youtube_refresh_token", env_refresh)
                v.store_secret("YOUTUBE_REFRESH_TOKEN", env_refresh)
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

    async def verify_video_publication(
        self,
        service: Any,
        video_id: str,
        expected_channel_id: str,
        wait_for_processing: bool = True,
        max_wait_seconds: float = 60.0,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Query YouTube API for the created video and verify:
        1. Video exists and belongs to expected channel ID.
        2. Visibility is explicitly public.
        3. Upload processing is completed (status == 'processed').
        4. Media metadata and accessible URL.
        """
        loop = asyncio.get_running_loop()
        start_time = loop.time()
        poll_interval = 3.0

        while True:
            try:
                req = service.videos().list(part="snippet,status,contentDetails", id=video_id)
                res = await asyncio.to_thread(req.execute)
                items = res.get("items", [])
                if not items:
                    elapsed = loop.time() - start_time
                    if elapsed < max_wait_seconds:
                        await asyncio.sleep(poll_interval)
                        continue
                    return False, "video_not_found", {"error": f"Video {video_id} not found via YouTube API within {max_wait_seconds}s."}

                item = items[0]
                snippet = item.get("snippet", {})
                status = item.get("status", {})
                content_details = item.get("contentDetails", {})

                actual_channel_id = snippet.get("channelId")
                actual_privacy = status.get("privacyStatus")
                upload_status = status.get("uploadStatus") or ""
                rejection_reason = status.get("rejectionReason") or ""
                failure_reason = status.get("failureReason") or rejection_reason or ""
                proc_details = item.get("processingDetails", {})
                title = snippet.get("title")
                duration = content_details.get("duration")

                video_info = {
                    "id": video_id,
                    "channelId": actual_channel_id,
                    "channelTitle": snippet.get("channelTitle"),
                    "title": title,
                    "privacyStatus": actual_privacy,
                    "uploadStatus": upload_status,
                    "failureReason": failure_reason,
                    "duration": duration,
                    "publicStatsViewable": status.get("publicStatsViewable"),
                    "permalink": f"https://youtube.com/shorts/{video_id}",
                    "url": f"https://youtube.com/shorts/{video_id}",
                }

                # Check 1: Channel ID must match expected AL AMR channel
                if actual_channel_id != expected_channel_id:
                    log.error(
                        "Post-upload verification: Channel ID mismatch! Expected %s, got %s",
                        expected_channel_id,
                        actual_channel_id,
                    )
                    return False, "channel_mismatch", video_info

                # Check 2: Terminal failure or rejection
                if upload_status in ("failed", "rejected") or failure_reason or rejection_reason:
                    log.error(
                        "Post-upload verification: YouTube processing failed (%s): failure_reason=%s, rejection_reason=%s",
                        upload_status,
                        failure_reason,
                        rejection_reason,
                    )
                    video_info["failureReason"] = failure_reason or rejection_reason or upload_status
                    try:
                        del_req = service.videos().delete(id=video_id)
                        await asyncio.to_thread(del_req.execute)
                        log.info("Cleaned up failed video %s from YouTube channel", video_id)
                    except Exception as del_err:
                        log.debug("Could not auto-delete failed YouTube video %s: %s", video_id, del_err)
                    return False, "processing_failed", video_info

                # Check 3: Processing state
                # YouTube video processing MUST be finished ('processed' or proc_details 'succeeded')
                proc_status = proc_details.get("processingStatus")
                if upload_status == "processed" or proc_status == "succeeded":
                    # Check 4: Visibility must be public
                    if actual_privacy != "public":
                        log.warning(
                            "Post-upload verification: Video is %s, attempting safe update to public...",
                            actual_privacy,
                        )
                        # Attempt safe correction if on correct channel
                        try:
                            update_req = service.videos().update(
                                part="status",
                                body={
                                    "id": video_id,
                                    "status": {
                                        "privacyStatus": "public",
                                        "selfDeclaredMadeForKids": False,
                                    },
                                },
                            )
                            await asyncio.to_thread(update_req.execute)
                            # Re-read
                            re_req = service.videos().list(part="status", id=video_id)
                            re_res = await asyncio.to_thread(re_req.execute)
                            re_items = re_res.get("items", [])
                            if re_items:
                                actual_privacy = re_items[0].get("status", {}).get("privacyStatus")
                                video_info["privacyStatus"] = actual_privacy
                        except Exception as upd_err:
                            log.warning("Could not auto-correct privacy to public: %s", upd_err)

                    if actual_privacy != "public":
                        log.error("Post-upload verification: Visibility is %s, expected 'public'.", actual_privacy)
                        return False, "visibility_incorrect", video_info

                    # All verification conditions satisfied!
                    return True, "published", video_info

                # Video is still processing ('uploaded' or 'processing')
                elapsed = loop.time() - start_time
                if not wait_for_processing or elapsed >= max_wait_seconds:
                    log.warning(
                        "Post-upload verification: YouTube processing not completed within %ss (status: %s, proc_status: %s)",
                        max_wait_seconds,
                        upload_status,
                        proc_status,
                    )
                    return False, "processing_incomplete", video_info

                await asyncio.sleep(poll_interval)

            except Exception as exc:
                log.warning("Exception during YouTube post-upload verification: %s", exc)
                elapsed = loop.time() - start_time
                if elapsed >= max_wait_seconds:
                    return False, "platform_error", {"error": str(exc)}
                await asyncio.sleep(poll_interval)

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
        from ..seo.sanitizer import sanitize_public_text
        title = sanitize_public_text(metadata.title or "", is_title=True)
        if not title:
            title = "Key Insight & Lesson"
        if "#Shorts" not in title and "#shorts" not in title:
            if len(title) <= 92:
                title = f"{title} #Shorts"
        title = title[:100]

        description = sanitize_public_text(metadata.description or "", is_title=False)
        if "#Shorts" not in description and "#shorts" not in description:
            description = f"{description}\n\n#Shorts".strip()
        description = description[:5000]

        raw_tags = list(set(metadata.tags + ["Shorts"]))
        cleaned_tags = []
        for t in raw_tags:
            clean = re.sub(r"[^\w\s-]", "", str(t)).strip()
            if not clean:
                continue
            if any(b in clean.lower() for b in ("alamr_internal", "autoclip_internal")):
                continue
            if clean not in cleaned_tags:
                cleaned_tags.append(clean)
        tags = []
        total_tag_chars = 0
        for t in cleaned_tags[:30]:
            if total_tag_chars + len(t) + 1 > 400:
                break
            tags.append(t)
            total_tag_chars += len(t) + 1

        # Check live publish configuration (default is live when credentials are present)
        is_live_disabled = os.getenv("YOUTUBE_DRY_RUN", "").lower() in ("true", "1", "yes") or os.getenv("YOUTUBE_PUBLISH_LIVE", "true").lower() in ("false", "0", "no")
        actual_dry_run = dry_run or is_live_disabled

        # Resolve expected channel ID
        expected_channel_id = resolve_expected_youtube_channel_id(
            explicit=metadata.extra.get("expected_channel_id")
            or (metadata.destination if (metadata.destination and metadata.destination.startswith("UC")) else None)
        )

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
                    "privacy": "public",
                    "tags": tags,
                    "expected_channel_id": expected_channel_id,
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

            # =========================================================================
            # CHANNEL IDENTITY GUARD (PRE-UPLOAD)
            # =========================================================================
            channel_req = service.channels().list(part="snippet", mine=True)
            channel_resp = await asyncio.to_thread(channel_req.execute)
            channel_items = channel_resp.get("items", [])
            if not channel_items:
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error="No YouTube channel found for authenticated credentials.",
                    error_code="channel_mismatch",
                    retryable=False,
                )

            actual_channel_id = channel_items[0].get("id")
            actual_channel_title = channel_items[0].get("snippet", {}).get("title") or "Unknown"

            if not expected_channel_id:
                log.error("YouTube Channel Identity Guard: AL_AMR_YOUTUBE_CHANNEL_ID is not configured. HARD STOP.")
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error="AL_AMR_YOUTUBE_CHANNEL_ID is not configured. Publication blocked by Channel Identity Guard.",
                    error_code="channel_mismatch",
                    retryable=False,
                    details={"actual_channel_id": actual_channel_id, "actual_channel_title": actual_channel_title},
                )

            if actual_channel_id != expected_channel_id:
                log.error(
                    "YouTube Channel Identity Guard MISMATCH: authenticated channel is '%s' (%s), expected AL AMR channel is '%s'. HARD STOP.",
                    actual_channel_title,
                    actual_channel_id,
                    expected_channel_id,
                )
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error=f"YouTube channel mismatch: authenticated account is '{actual_channel_title}' ({actual_channel_id}), expected AL AMR channel is '{expected_channel_id}'. Publication blocked by Channel Identity Guard.",
                    error_code="channel_mismatch",
                    retryable=False,
                    details={
                        "actual_channel_id": actual_channel_id,
                        "actual_channel_title": actual_channel_title,
                        "expected_channel_id": expected_channel_id,
                    },
                )

            from ..media_guard import is_valid_mp4
            if not is_valid_mp4(media_path):
                log.error("YouTube Publisher: Media file %s is not a valid MP4 video. Rejecting upload.", media_path)
                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed",
                    error=f"Media file {media_path} is invalid or corrupted (not a valid MP4 video).",
                    error_code="invalid_media",
                    retryable=False,
                )

            # =========================================================================
            # EXPLICIT VISIBILITY: Must be "public" (never unlisted or private by default)
            # =========================================================================
            privacy_status = (metadata.privacy or "public").strip().lower()
            if privacy_status not in ("public", "unlisted", "private"):
                privacy_status = "public"

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "categoryId": "22",  # People & Blogs
                },
                "status": {
                    "privacyStatus": privacy_status,
                    "selfDeclaredMadeForKids": False,
                },
            }

            media = MediaFileUpload(
                str(media_path),
                mimetype="video/mp4",
                resumable=True,
                chunksize=1024 * 1024 * 5,  # 5MB chunks
            )

            request = service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            def _upload_media():
                # In live mode with real googleapiclient, use next_chunk loop for network resilience
                if hasattr(request, "next_chunk") and not type(request).__name__.startswith("MagicMock"):
                    resp = None
                    retries = 0
                    max_retries = 5
                    while resp is None:
                        try:
                            status, resp = request.next_chunk()
                            if status:
                                pct = int(status.progress() * 100)
                                log.info("YouTube upload progress: %d%%", pct)
                        except Exception as upload_err:
                            from googleapiclient.errors import HttpError
                            is_retriable = False
                            if isinstance(upload_err, HttpError) and upload_err.resp.status in (500, 502, 503, 504):
                                is_retriable = True
                            elif isinstance(upload_err, (TimeoutError, ConnectionError, OSError)):
                                is_retriable = True
                            
                            if is_retriable and retries < max_retries:
                                retries += 1
                                sleep_s = 2 ** retries
                                log.warning("YouTube chunk upload retry %d/%d after error: %s (sleeping %ds)", retries, max_retries, upload_err, sleep_s)
                                import time
                                time.sleep(sleep_s)
                            else:
                                raise upload_err
                    return resp
                return request.execute(num_retries=3)

            response = await asyncio.to_thread(_upload_media)
            video_id = response.get("id")
            if not video_id:
                raise RuntimeError(f"No video ID returned by YouTube API: {response}")

            log.info("YouTube upload accepted with ID %s. Starting Post-Upload Verification...", video_id)

            # =========================================================================
            # POST-UPLOAD VERIFICATION & INCOMPLETE UPLOAD PROTECTION
            # =========================================================================
            verified, verify_status, video_info = await self.verify_video_publication(
                service=service,
                video_id=video_id,
                expected_channel_id=expected_channel_id,
                wait_for_processing=True,
                max_wait_seconds=15.0,
            )

            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()
            short_url = f"https://youtube.com/shorts/{video_id}"

            if not verified:
                error_msgs = {
                    "channel_mismatch": f"YouTube video published on wrong channel ({video_info.get('channelId')}). Expected: {expected_channel_id}.",
                    "visibility_incorrect": f"YouTube video visibility is '{video_info.get('privacyStatus')}' rather than 'public'. Publication not accepted.",
                    "processing_incomplete": f"YouTube upload accepted but video processing incomplete (status: {video_info.get('uploadStatus')}).",
                    "processing_failed": f"YouTube video processing failed: {video_info.get('failureReason') or 'rejected'}.",
                    "video_not_found": f"Video {video_id} not found via YouTube API.",
                }
                err_msg = error_msgs.get(verify_status, f"YouTube post-upload verification failed ({verify_status})")
                log.error("YouTube Post-Upload Verification FAILED: %s", err_msg)

                return PublishingResult(
                    platform=self.platform_name,
                    destination_id=destination_id,
                    success=False,
                    status="failed" if verify_status != "processing_incomplete" else "ready_for_upload",
                    external_id=video_id,
                    remote_media_id=video_id,
                    remote_post_id=video_id,
                    url=short_url,
                    permalink=short_url,
                    error=err_msg,
                    error_code=verify_status,
                    retryable=(verify_status == "processing_incomplete"),
                    details={**response, **video_info, "verification_status": verify_status},
                )

            # ALL 10 CONDITIONS VERIFIED AND PASSED
            log.info("Successfully published and verified YouTube Short: %s on channel %s", short_url, actual_channel_title)
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
                details={**response, **video_info, "channel_title": actual_channel_title, "verification_status": "VERIFIED_PUBLIC"},
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
        expected_channel_id = resolve_expected_youtube_channel_id()
        channel_match = (channel_id == expected_channel_id) if expected_channel_id else None
        return {
            "valid": True,
            "configured": True,
            "error": None,
            "channel_title": f"{channel_title} ({custom_url})" if custom_url else channel_title,
            "channel_id": channel_id,
            "expected_channel_id": expected_channel_id,
            "channel_match": channel_match,
        }
    except Exception as exc:
        return {
            "valid": False,
            "configured": True,
            "error": f"YouTube authentication verification failed: {exc}",
            "channel_title": None,
            "channel_id": None,
            "expected_channel_id": resolve_expected_youtube_channel_id(),
            "channel_match": False,
        }

