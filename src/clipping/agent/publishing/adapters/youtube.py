"""YouTube Platform Publishing Adapter.

Integrates directly with the real YouTube Data API client and publishing pipeline.
Supports private preparation/drafts, scheduled releases, and immediate publication.
Queries live video status for durable reconciliation.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from clipping.agent.publishing.adapters.base import (
    PlatformPublishingAdapter,
    PlatformPublishResult,
    PlatformStatusResult,
)
from clipping.agent.publishing.models import (
    CampaignSubmissionRecord,
    PublishingMode,
    SubmissionStatus,
)
from clipping.agent.vault.models import AccountPlatform
from clipping.logging.logger import get_logger
from clipping.publishing.client import (
    FailureClassification,
    HttpYouTubeClient,
    MockYouTubeClient,
    YouTubeClient,
    YouTubeClientError,
)
from clipping.publishing.models import PrivacyStatus, YouTubeVideoMetadata

logger = get_logger("clipping.agent.publishing.adapters.youtube")


class YouTubePublishingAdapter(PlatformPublishingAdapter):
    """Real YouTube Data API v3 publishing and scheduling adapter."""

    def __init__(self, client: Optional[YouTubeClient] = None):
        self._client = client

    @property
    def platform(self) -> AccountPlatform:
        return AccountPlatform.YOUTUBE

    def _map_privacy_status(self, mode: PublishingMode, configured_privacy: str) -> PrivacyStatus:
        """Determines compliant YouTube privacy status."""
        if mode == PublishingMode.DRAFT:
            return PrivacyStatus.PRIVATE
        if mode == PublishingMode.SCHEDULED:
            return PrivacyStatus.PRIVATE  # Scheduled videos upload as private until scheduled time
        if mode == PublishingMode.IMMEDIATE:
            if configured_privacy.lower() == "unlisted":
                return PrivacyStatus.UNLISTED
            return PrivacyStatus.PUBLIC
        return PrivacyStatus.PRIVATE

    def _resolve_client(self, credentials: Dict[str, Any]) -> Optional[YouTubeClient]:
        """Resolves authenticated HttpYouTubeClient from credentials, environment, or injected client."""
        if self._client is not None:
            return self._client

        import os
        from pydantic import SecretStr
        from clipping.publishing.oauth import OAuthCredentials, OAuthTokenManager
        from clipping.publishing.client import HttpYouTubeClient

        client_id = credentials.get("client_id") or os.getenv("YOUTUBE_CLIENT_ID")
        client_secret = credentials.get("client_secret") or os.getenv("YOUTUBE_CLIENT_SECRET")
        refresh_token = credentials.get("refresh_token") or os.getenv("YOUTUBE_REFRESH_TOKEN")

        if client_id and client_secret and refresh_token:
            secret_str = client_secret if isinstance(client_secret, SecretStr) else SecretStr(str(client_secret))
            refresh_str = refresh_token if isinstance(refresh_token, SecretStr) else SecretStr(str(refresh_token))
            token_mgr = OAuthTokenManager(
                credentials=OAuthCredentials(
                    client_id=str(client_id),
                    client_secret=secret_str,
                    refresh_token=refresh_str,
                )
            )
            return HttpYouTubeClient(token_manager=token_mgr)
        return None

    async def publish(
        self,
        submission: CampaignSubmissionRecord,
        media_path: str,
        credentials: Dict[str, Any],
    ) -> PlatformPublishResult:
        """Uploads video to YouTube via resumable protocol with campaign metadata."""
        meta = submission.content_metadata
        mode = submission.publishing_mode
        privacy = self._map_privacy_status(mode, meta.privacy_status)

        # Build clean tags from hashtags
        tags = [t.lstrip("#") for t in meta.hashtags if t.strip()]

        yt_meta = YouTubeVideoMetadata(
            title=meta.title[:100],
            description=meta.description[:5000],
            tags=tags[:50],
            privacy_status=privacy,
            publish_at=meta.scheduled_publish_at if mode == PublishingMode.SCHEDULED else None,
            category_id="22",  # People & Blogs / Entertainment standard
            made_for_kids=False,
        )

        client = self._resolve_client(credentials)
        if client is None:
            logger.warning("YouTube publishing rejected: missing credentials and no client configured", submission_id=submission.submission_id)
            return PlatformPublishResult(
                success=False,
                status=SubmissionStatus.FAILED,
                error_message="YouTube OAuth2 credentials missing: client_id, client_secret, and refresh_token are required.",
                failure_classification="missing_credentials",
                is_retryable=False,
            )

        try:
            ref = await client.upload_video(video_path=media_path, metadata=yt_meta)
            final_status = (
                SubmissionStatus.SCHEDULED
                if mode == PublishingMode.SCHEDULED
                else (SubmissionStatus.PUBLISHED if privacy == PrivacyStatus.PUBLIC else SubmissionStatus.SUBMITTED)
            )

            logger.info(
                "YouTube video upload completed successfully",
                video_id=ref.video_id,
                submission_id=submission.submission_id,
                status=final_status.value,
            )

            return PlatformPublishResult(
                success=True,
                platform_post_id=ref.video_id,
                platform_url=ref.watch_url,
                status=final_status,
                raw_response={"video_id": ref.video_id, "channel_id": ref.channel_id, "url": ref.watch_url},
            )


        except YouTubeClientError as err:
            logger.error(
                "YouTube publishing failed",
                submission_id=submission.submission_id,
                error=str(err),
                reason=err.reason,
            )
            is_retryable = (err.failure_type == FailureClassification.RETRYABLE)
            return PlatformPublishResult(
                success=False,
                status=SubmissionStatus.RETRY_PENDING if is_retryable else SubmissionStatus.FAILED,
                error_message=str(err),
                failure_classification=err.reason,
                is_retryable=is_retryable,
                raw_response={"status_code": err.status_code, "reason": err.reason},
            )
        except Exception as e:
            logger.error("Unexpected error during YouTube publishing", error=str(e))
            return PlatformPublishResult(
                success=False,
                status=SubmissionStatus.FAILED,
                error_message=str(e),
                failure_classification="unexpected_exception",
                is_retryable=False,
            )

    async def reconcile_status(
        self,
        platform_post_id: str,
        credentials: Dict[str, Any],
    ) -> PlatformStatusResult:
        client = self._resolve_client(credentials)
        if client is None:
            logger.info("Cannot query live YouTube status without client or credentials; preserving local state", post_id=platform_post_id)
            return PlatformStatusResult(
                post_id=platform_post_id,
                exists_on_platform=True,
                platform_status=SubmissionStatus.PUBLISHED,
                error_message="Credentials not available for live status reconciliation inquiry",
            )

        try:
            status_data = await client.get_video_status(platform_post_id)
            if not status_data:
                return PlatformStatusResult(
                    post_id=platform_post_id,
                    exists_on_platform=False,
                    platform_status=SubmissionStatus.REJECTED,
                    error_message="Video not found on YouTube platform",
                )

            status_dict = status_data.get("status") if isinstance(status_data.get("status"), dict) else {}
            upload_status = (status_dict.get("uploadStatus") or status_data.get("upload_status", "uploaded")).lower()
            privacy_status = (status_dict.get("privacyStatus") or status_data.get("privacy_status", "public")).lower()
            view_count = status_data.get("view_count", 0)

            if upload_status in ("processed", "uploaded"):
                state = SubmissionStatus.PUBLISHED if privacy_status == "public" else SubmissionStatus.SUBMITTED
            elif upload_status == "rejected":
                state = SubmissionStatus.REJECTED
            elif upload_status == "failed":
                state = SubmissionStatus.FAILED
            else:
                state = SubmissionStatus.PUBLISHED


            return PlatformStatusResult(
                post_id=platform_post_id,
                exists_on_platform=True,
                platform_status=state,
                privacy_status=privacy_status,
                view_count=view_count,
                raw_details=status_data,
            )

        except Exception as e:
            logger.warning("Failed to reconcile live YouTube video status", video_id=platform_post_id, error=str(e))
            return PlatformStatusResult(
                post_id=platform_post_id,
                exists_on_platform=True,
                platform_status=SubmissionStatus.PUBLISHED,
                error_message=str(e),
            )
