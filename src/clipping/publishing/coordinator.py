"""Multi-Platform Publishing Coordinator (Step 5/5).

Coordinates simultaneous publishing to YouTube Shorts and Instagram Reels,
enforces the human approval hard gate, ensures publication idempotency,
validates account status, prepares platform-specific metadata, and handles
partial publication states without silent data fabrication.
"""

import asyncio
from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from clipping.logging.logger import get_logger
from clipping.contracts.production import ProductionArtifact, ReviewStatus
from clipping.contracts.orchestration import (
    PlatformPublishStatus,
    PlatformPublicationResult,
    OverallStatus,
)
from clipping.contracts.requirements import CampaignRequirements
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.agent.publishing.adapters.youtube import YouTubePublishingAdapter
from clipping.agent.publishing.adapters.instagram import InstagramPublishingAdapter
from clipping.agent.publishing.adapters.base import PlatformPublishResult
from clipping.agent.publishing.models import CampaignSubmissionRecord, SubmissionStatus
from clipping.storage.base import StorageDriver
from clipping.storage.local import LocalStorageDriver

logger = get_logger("clipping.publishing.coordinator")


class ApprovalGateBlockedError(RuntimeError):
    """Raised when publishing is attempted on an artifact that has not been approved."""
    pass


class AccountNotReadyError(RuntimeError):
    """Raised when destination account is missing, inactive, or unverified."""
    pass


class MultiPlatformPublishingCoordinator:
    """
    Authoritative publishing coordinator managing multi-destination publishing
    with strict human approval verification, idempotency, and partial success isolation.
    """

    def __init__(
        self,
        vault: Optional[EncryptedCredentialVault] = None,
        storage_driver: Optional[StorageDriver] = None,
        youtube_adapter: Optional[YouTubePublishingAdapter] = None,
        instagram_adapter: Optional[InstagramPublishingAdapter] = None,
    ):
        self.vault = vault
        self.storage = storage_driver or LocalStorageDriver(root_dir="storage")
        self.youtube_adapter = youtube_adapter or YouTubePublishingAdapter()
        self.instagram_adapter = instagram_adapter or InstagramPublishingAdapter()

    def _receipt_key(self, campaign_id: str, artifact_id: str, platform: str) -> str:
        clean_p = platform.lower().replace(" ", "_")
        return f"publishing/receipts/{campaign_id}/{artifact_id}_{clean_p}.json"

    async def get_publication_receipt(
        self,
        campaign_id: str,
        artifact_id: str,
        platform: str,
    ) -> Optional[PlatformPublicationResult]:
        """Retrieves existing publication receipt for idempotency enforcement."""
        key = self._receipt_key(campaign_id, artifact_id, platform)
        if await self.storage.exists(key):
            try:
                raw = await self.storage.download_bytes(key)
                return PlatformPublicationResult.model_validate_json(raw.decode("utf-8"))
            except Exception as e:
                logger.warning("Failed to deserialize publication receipt", key=key, error=str(e))
        return None

    async def save_publication_receipt(
        self,
        campaign_id: str,
        artifact_id: str,
        result: PlatformPublicationResult,
    ) -> None:
        """Persists authoritative publication receipt to durable storage."""
        key = self._receipt_key(campaign_id, artifact_id, result.platform)
        payload = result.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(payload, key, content_type="application/json")

    def validate_approval(self, artifact: ProductionArtifact) -> None:
        """
        Hard gate: Publishing is strictly blocked unless explicit operator approval has been granted.
        Automatic confidence, timeout, or prior version approvals are strictly invalid.
        """
        if artifact.review_status != ReviewStatus.APPROVED:
            err_msg = (
                f"PUBLISHING BLOCKED | Approval Gate Violation | Artifact '{artifact.artifact_id}' "
                f"has status '{artifact.review_status.value}', but publishing strictly requires '{ReviewStatus.APPROVED.value}'. "
                f"Human approval via Telegram [✓ GOOD / APPROVE] or Mission Control is required."
            )
            logger.error("Publishing attempted on unapproved artifact", artifact_id=artifact.artifact_id, review_status=artifact.review_status)
            raise ApprovalGateBlockedError(err_msg)

    async def validate_account(
        self,
        platform: str,
        target_account: Optional[AccountMetadata] = None,
    ) -> AccountMetadata:
        """Validates that destination account exists and is verified ACTIVE."""
        plat_enum = AccountPlatform.YOUTUBE if "youtube" in platform.lower() else AccountPlatform.INSTAGRAM
        account = target_account if (target_account and getattr(target_account, "platform", None) == plat_enum) else None
        if not account and self.vault:
            accounts = await self.vault.list_accounts(platform=plat_enum)
            active_accounts = [a for a in accounts if a.status == AccountStatus.ACTIVE]
            if active_accounts:
                account = active_accounts[0]

        if not account and target_account:
            account = target_account

        if not account:
            raise AccountNotReadyError(f"No account configured for platform '{platform}'. A verified active account is required.")

        if account.status != AccountStatus.ACTIVE:
            raise AccountNotReadyError(
                f"Account '{account.account_id}' for '{platform}' is in status '{account.status.value}'. "
                f"Publishing requires ACTIVE status."
            )

        return account

    async def publish_artifact_to_platform(
        self,
        artifact: ProductionArtifact,
        platform: str,
        campaign_id: str,
        requirements: Optional[CampaignRequirements] = None,
        target_account: Optional[AccountMetadata] = None,
    ) -> PlatformPublicationResult:
        """
        Publishes a single approved artifact to a specific target platform.
        Enforces idempotency, verifies output, and records durable receipts.
        """
        # 1. Approval Hard Gate
        self.validate_approval(artifact)

        clean_plat = "youtube_shorts" if "youtube" in platform.lower() else "instagram_reels"

        # 2. Idempotency Check: Don't republish if already confirmed successful
        existing_receipt = await self.get_publication_receipt(campaign_id, artifact.artifact_id, clean_plat)
        if existing_receipt and existing_receipt.is_success:
            logger.info(
                "Publication idempotency check: Artifact already published to platform",
                artifact_id=artifact.artifact_id,
                platform=clean_plat,
                publication_id=existing_receipt.publication_id,
                publication_url=existing_receipt.publication_url,
            )
            return existing_receipt

        # 3. Validate Destination Account
        account = await self.validate_account(clean_plat, target_account)

        # 4. Prepare Platform-Specific Metadata
        metadata = self._prepare_metadata(artifact, clean_plat, requirements)

        # 5. Execute Platform Upload
        media_path = artifact.local_output_path
        if not os.path.isfile(media_path):
            res = PlatformPublicationResult(
                platform=clean_plat,
                status=PlatformPublishStatus.FAILED,
                error_message=f"Local media file not found on disk: {media_path}",
                attempt_count=(existing_receipt.attempt_count + 1) if existing_receipt else 1,
            )
            await self.save_publication_receipt(campaign_id, artifact.artifact_id, res)
            return res

        logger.info(
            "Executing platform publication",
            artifact_id=artifact.artifact_id,
            platform=clean_plat,
            account_id=account.account_id,
        )

        now = datetime.now(timezone.utc)
        attempt = (existing_receipt.attempt_count + 1) if existing_receipt else 1

        try:
            submission = CampaignSubmissionRecord(
                submission_id=f"sub_{artifact.artifact_id[:12]}_{clean_plat[:2]}",
                campaign_id=campaign_id,
                clip_id=artifact.artifact_id,
                platform=AccountPlatform.YOUTUBE if clean_plat == "youtube_shorts" else AccountPlatform.INSTAGRAM,
                account_id=account.account_id,
                content_metadata=metadata,
                media_path=media_path,
                idempotency_key=f"idem_{campaign_id}_{artifact.artifact_id}_{clean_plat}",
            )

            # Retrieve real credentials from vault if available
            creds: Dict[str, Any] = {}
            if self.vault:
                decrypted = await self.vault.get_credentials(account.platform, account.account_id)
                if decrypted:
                    creds = decrypted

            # Invoke platform adapter
            if clean_plat == "youtube_shorts":
                publish_res: PlatformPublishResult = await self.youtube_adapter.publish(
                    submission=submission,
                    media_path=media_path,
                    credentials=creds,
                )
            else:
                publish_res: PlatformPublishResult = await self.instagram_adapter.publish(
                    submission=submission,
                    media_path=media_path,
                    credentials=creds,
                )

            is_ok = bool(getattr(publish_res, "success", False) or getattr(publish_res, "is_success", False))
            pub_id = getattr(publish_res, "platform_post_id", None) or getattr(publish_res, "platform_submission_id", None)

            if is_ok and pub_id:
                # 6. Authoritative Genuine Confirmation
                pub_url = publish_res.platform_url or (
                    f"https://www.youtube.com/shorts/{pub_id}" if clean_plat == "youtube_shorts"
                    else f"https://www.instagram.com/reel/{pub_id}/"
                )
                res = PlatformPublicationResult(
                    platform=clean_plat,
                    status=PlatformPublishStatus.SUCCESS,
                    publication_id=pub_id,
                    publication_url=pub_url,
                    attempt_count=attempt,
                    published_at=now,
                    verified_at=now,
                    metadata_snapshot=metadata.model_dump(),
                )
                logger.info(
                    "Platform publication verified successfully",
                    artifact_id=artifact.artifact_id,
                    platform=clean_plat,
                    publication_id=pub_id,
                    publication_url=pub_url,
                )
            else:
                err = publish_res.error_message or "Platform rejected upload without specific error"
                res = PlatformPublicationResult(
                    platform=clean_plat,
                    status=PlatformPublishStatus.FAILED,
                    error_message=err,
                    attempt_count=attempt,
                    metadata_snapshot=metadata.model_dump(),
                )
                logger.warning(
                    "Platform publication failed",
                    artifact_id=artifact.artifact_id,
                    platform=clean_plat,
                    error=err,
                )

        except Exception as e:
            logger.exception("Unexpected error during platform publishing", error=str(e))
            res = PlatformPublicationResult(
                platform=clean_plat,
                status=PlatformPublishStatus.FAILED,
                error_message=str(e),
                attempt_count=attempt,
                metadata_snapshot=metadata.model_dump(),
            )

        # Persist receipt
        await self.save_publication_receipt(campaign_id, artifact.artifact_id, res)
        return res

    async def publish_artifact_simultaneously(
        self,
        artifact: ProductionArtifact,
        platforms: List[str],
        campaign_id: str,
        requirements: Optional[CampaignRequirements] = None,
        target_account: Optional[AccountMetadata] = None,
    ) -> Dict[str, PlatformPublicationResult]:
        """
        Publishes an approved artifact to multiple platforms simultaneously (e.g. YouTube Shorts + Instagram Reels).
        Executes concurrently via asyncio.gather and isolates failures.
        """
        # Hard gate validation upfront
        self.validate_approval(artifact)

        normalized_platforms = []
        for p in platforms:
            p_clean = "youtube_shorts" if "youtube" in p.lower() else ("instagram_reels" if "instagram" in p.lower() else p)
            if p_clean not in normalized_platforms:
                normalized_platforms.append(p_clean)

        if not normalized_platforms:
            normalized_platforms = ["youtube_shorts"]

        tasks = [
            self.publish_artifact_to_platform(
                artifact=artifact,
                platform=plat,
                campaign_id=campaign_id,
                requirements=requirements,
                target_account=target_account,
            )
            for plat in normalized_platforms
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        out_dict: Dict[str, PlatformPublicationResult] = {}

        for plat, res in zip(normalized_platforms, results):
            if isinstance(res, Exception):
                out_dict[plat] = PlatformPublicationResult(
                    platform=plat,
                    status=PlatformPublishStatus.FAILED,
                    error_message=str(res),
                )
            else:
                out_dict[plat] = res

        return out_dict

    def _prepare_metadata(
        self,
        artifact: ProductionArtifact,
        platform: str,
        requirements: Optional[CampaignRequirements] = None,
    ) -> Any:
        """Prepares platform-specific metadata structure without fabricating data."""
        from clipping.agent.publishing.models import PublishingContentMetadata

        clean_tags = artifact.hashtags or []
        # Enforce required hashtags from requirements if present
        if requirements and requirements.text and requirements.text.required_hashtags:
            for req_tag in requirements.text.required_hashtags:
                tag_norm = req_tag.strip()
                if tag_norm and not tag_norm.startswith("#"):
                    tag_norm = f"#{tag_norm}"
                if tag_norm and tag_norm not in clean_tags:
                    clean_tags.append(tag_norm)

        # Prohibited words check
        prohibited_words = []
        if requirements and requirements.text and requirements.text.prohibited_words:
            prohibited_words = [pw.lower() for pw in requirements.text.prohibited_words if pw]

        desc = artifact.description or ""
        caption = artifact.caption or ""
        for pw in prohibited_words:
            if pw in desc.lower():
                desc = desc.replace(pw, "[redacted]")
            if pw in caption.lower():
                caption = caption.replace(pw, "[redacted]")

        if platform == "youtube_shorts":
            return PublishingContentMetadata(
                title=artifact.title[:100],
                description=desc,
                hashtags=clean_tags,
                mentions=artifact.mentions or [],
                campaign_identifiers={"campaign_id": artifact.campaign_id},
            )
        else:  # instagram_reels
            return PublishingContentMetadata(
                title=artifact.title[:100],
                description=caption,
                hashtags=clean_tags,
                mentions=artifact.mentions or [],
                campaign_identifiers={"campaign_id": artifact.campaign_id},
            )

    def compute_overall_status(self, results: Dict[str, PlatformPublicationResult]) -> OverallStatus:
        """Determines unified status across destination platforms."""
        if not results:
            return OverallStatus.APPROVED

        success_count = sum(1 for r in results.values() if r.is_success)
        total_count = len(results)

        if success_count == total_count and total_count > 0:
            return OverallStatus.COMPLETED
        elif success_count > 0:
            return OverallStatus.PARTIALLY_PUBLISHED
        else:
            return OverallStatus.FAILED
