"""YouTube Data API v3 Client Abstraction with Resumable Uploads & Mock."""

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Union, Tuple
import httpx
from clipping.publishing.models import (
    YouTubeVideoMetadata,
    YouTubeVideoReference,
    FailureClassification,
)
from clipping.publishing.oauth import OAuthTokenManager
from clipping.logging.logger import get_logger

logger = get_logger("clipping.publishing.client")


class YouTubeClientError(Exception):
    """Base exception for YouTube API interaction failures with granular error reason."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        reason: str = "unknown",
        failure_type: FailureClassification = FailureClassification.NON_RETRYABLE,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.failure_type = failure_type


def parse_youtube_api_error(status_code: int, response_text: str) -> Tuple[str, str, FailureClassification]:
    """
    Parses Google API / YouTube Data API v3 error response structure:
    Extracts reason code (quotaExceeded, uploadLimitExceeded, rateLimitExceeded, etc.)
    and classifies into RETRYABLE vs NON_RETRYABLE.
    """
    reason = "unknown"
    message = response_text
    try:
        data = json.loads(response_text)
        err = data.get("error", {})
        message = err.get("message", response_text)
        errors = err.get("errors", [])
        if errors and isinstance(errors, list):
            reason = errors[0].get("reason", "unknown")
    except Exception:
        pass

    # 1. Quota & Limits (Retryable when project quota resets or channel rolling window clears)
    if reason == "quotaExceeded":
        return reason, f"YouTube API project quota exceeded: {message}", FailureClassification.RETRYABLE
    if reason == "uploadLimitExceeded":
        return reason, f"YouTube channel-level daily upload limit reached: {message}", FailureClassification.RETRYABLE
    if reason in ("rateLimitExceeded", "userRateLimitExceeded") or status_code == 429:
        return reason, f"YouTube rate limit exceeded: {message}", FailureClassification.RETRYABLE

    # 2. Transient Server & Network Errors (Retryable)
    if status_code >= 500:
        return reason, f"YouTube server error ({status_code}): {message}", FailureClassification.RETRYABLE

    # 3. Permanent / Non-Retryable Errors
    if status_code == 400:
        return reason, f"Bad request / invalid metadata ({status_code}): {message}", FailureClassification.NON_RETRYABLE
    if status_code == 401:
        return reason, f"Unauthorized / invalid credentials ({status_code}): {message}", FailureClassification.NON_RETRYABLE
    if status_code == 403:
        # Access not configured, insufficient permissions, suspended, forbidden
        return reason, f"Forbidden / permission denied ({status_code}, reason={reason}): {message}", FailureClassification.NON_RETRYABLE

    return reason, f"API error ({status_code}): {message}", FailureClassification.NON_RETRYABLE


class YouTubeClient(ABC):
    """Abstract interface decoupling the publishing service from live YouTube network calls."""

    @abstractmethod
    async def verify_channel(self, expected_channel_id: str) -> bool:
        """Verifies that the authenticated OAuth credentials match the expected YouTube channel."""
        pass

    @abstractmethod
    async def upload_video(
        self,
        video_path: str,
        metadata: YouTubeVideoMetadata,
    ) -> YouTubeVideoReference:
        """Uploads a video to YouTube using the resumable upload protocol."""
        pass

    @abstractmethod
    async def get_video_status(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Queries the current status and processing state of an uploaded video."""
        pass


class HttpYouTubeClient(YouTubeClient):
    """Production implementation of YouTube Data API v3 using HTTP and Google OAuth2."""

    def __init__(
        self,
        token_manager: OAuthTokenManager,
        api_base_url: str = "https://www.googleapis.com/youtube/v3",
        upload_base_url: str = "https://www.googleapis.com/upload/youtube/v3/videos",
    ):
        self.token_manager = token_manager
        self.api_base_url = api_base_url.rstrip("/")
        self.upload_base_url = upload_base_url.rstrip("/")

    async def verify_channel(self, expected_channel_id: str) -> bool:
        token = await self.token_manager.get_access_token()
        url = f"{self.api_base_url}/channels?part=snippet,contentDetails&mine=true"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.error("Failed to verify YouTube channel identity", status_code=resp.status_code)
                return False
            data = resp.json()
            items = data.get("items", [])
            if not items:
                logger.error("No YouTube channel found for authenticated user")
                return False

            actual_id = items[0].get("id")
            matches = (actual_id == expected_channel_id)
            if not matches:
                logger.error("YouTube channel identity mismatch", expected=expected_channel_id, actual=actual_id)
            return matches

    async def upload_video(
        self,
        video_path: str,
        metadata: YouTubeVideoMetadata,
    ) -> YouTubeVideoReference:
        if not os.path.isfile(video_path):
            raise YouTubeClientError(f"Video file not found: {video_path}")

        file_size = os.path.getsize(video_path)
        token = await self.token_manager.get_access_token()

        # 1. Initiate Resumable Upload Session
        init_url = f"{self.upload_base_url}?uploadType=resumable&part=snippet,status"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": "video/mp4",
        }

        body = {
            "snippet": {
                "title": metadata.title,
                "description": metadata.description,
                "tags": metadata.tags,
                "categoryId": "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": metadata.privacy_status.value,
                "selfDeclaredMadeForKids": False,
            },
        }

        if metadata.publish_at:
            # When scheduled, privacy must be private initially
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = metadata.publish_at.isoformat()

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                init_resp = await client.post(init_url, headers=headers, json=body)
                if init_resp.status_code not in (200, 201):
                    reason, msg, failure_type = parse_youtube_api_error(init_resp.status_code, init_resp.text)
                    raise YouTubeClientError(
                        msg,
                        status_code=init_resp.status_code,
                        reason=reason,
                        failure_type=failure_type,
                    )

                upload_url = init_resp.headers.get("Location")
                if not upload_url:
                    raise YouTubeClientError("Missing Location header in resumable upload response")

                # 2. Stream Media Content
                with open(video_path, "rb") as vf:
                    video_bytes = vf.read()

                put_headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(file_size),
                }
                put_resp = await client.put(upload_url, headers=put_headers, content=video_bytes)

                if put_resp.status_code in (200, 201):
                    result = put_resp.json()
                    video_id = result.get("id")
                    channel_id = result.get("snippet", {}).get("channelId", "")
                    if not video_id:
                        raise YouTubeClientError("Missing video ID in YouTube upload response")

                    watch_url = f"https://youtube.com/shorts/{video_id}"
                    logger.info("Successfully uploaded video to YouTube", video_id=video_id, watch_url=watch_url)
                    return YouTubeVideoReference(
                        video_id=video_id,
                        watch_url=watch_url,
                        channel_id=channel_id,
                    )
                else:
                    reason, msg, failure_type = parse_youtube_api_error(put_resp.status_code, put_resp.text)
                    raise YouTubeClientError(
                        f"Media upload failed: {msg}",
                        status_code=put_resp.status_code,
                        reason=reason,
                        failure_type=failure_type,
                    )

            except httpx.RequestError as e:
                raise YouTubeClientError(
                    f"Network error during upload: {str(e)}",
                    reason="networkError",
                    failure_type=FailureClassification.RETRYABLE,
                )

    async def get_video_status(self, video_id: str) -> Optional[Dict[str, Any]]:
        token = await self.token_manager.get_access_token()
        url = f"{self.api_base_url}/videos?part=snippet,status,processingDetails&id={video_id}"
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return None
            data = resp.json()
            items = data.get("items", [])
            return items[0] if items else None


class MockYouTubeClient(YouTubeClient):
    """Deterministic in-memory mock client for testing without network or credentials."""

    def __init__(self, expected_channel_id: str = "UC_TEST_CHANNEL_01"):
        self.expected_channel_id = expected_channel_id
        self.uploaded_videos: Dict[str, Dict[str, Any]] = {}
        self.simulate_network_error: bool = False
        self.simulate_quota_error: bool = False
        self.simulate_upload_limit_error: bool = False
        self.simulate_rate_limit_error: bool = False
        self.simulate_permission_error: bool = False
        self._next_video_id = 1000

    async def verify_channel(self, expected_channel_id: str) -> bool:
        return expected_channel_id == self.expected_channel_id

    async def upload_video(
        self,
        video_path: str,
        metadata: YouTubeVideoMetadata,
    ) -> YouTubeVideoReference:
        if self.simulate_network_error:
            raise YouTubeClientError("Mock network timeout", reason="networkTimeout", failure_type=FailureClassification.RETRYABLE)
        if self.simulate_quota_error:
            raise YouTubeClientError("Mock quota exceeded", status_code=403, reason="quotaExceeded", failure_type=FailureClassification.RETRYABLE)
        if self.simulate_upload_limit_error:
            raise YouTubeClientError("Mock channel upload limit reached", status_code=403, reason="uploadLimitExceeded", failure_type=FailureClassification.RETRYABLE)
        if self.simulate_rate_limit_error:
            raise YouTubeClientError("Mock rate limit exceeded", status_code=429, reason="rateLimitExceeded", failure_type=FailureClassification.RETRYABLE)
        if self.simulate_permission_error:
            raise YouTubeClientError("Mock permission denied", status_code=403, reason="insufficientPermissions", failure_type=FailureClassification.NON_RETRYABLE)

        vid_id = f"yt_mock_{self._next_video_id}"
        self._next_video_id += 1

        ref = YouTubeVideoReference(
            video_id=vid_id,
            watch_url=f"https://youtube.com/shorts/{vid_id}",
            channel_id=self.expected_channel_id,
        )
        self.uploaded_videos[vid_id] = {
            "metadata": metadata,
            "path": video_path,
            "reference": ref,
        }
        return ref

    async def get_video_status(self, video_id: str) -> Optional[Dict[str, Any]]:
        if video_id in self.uploaded_videos:
            return {"id": video_id, "status": {"uploadStatus": "processed"}}
        return None
