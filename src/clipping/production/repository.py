"""Durable storage and query repository for production artifacts, revisions, and operator interventions."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import TypeAdapter

from clipping.contracts.production import (
    ProductionArtifact,
    ProductionStatus,
    ReviewStatus,
    RevisionRecord,
    OperatorInterventionRecord,
)
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.production.repository")


class ProductionRepository:
    """
    Persists production artifacts, revisions, and intervention checkpoints to durable storage.
    Guarantees artifacts survive process restarts without duplicate renders or accidental publishing.
    """

    def __init__(self, storage_driver: StorageDriver):
        self.storage = storage_driver

    def _artifact_key(self, campaign_id: str, artifact_id: str) -> str:
        return f"production/artifacts/{campaign_id}/{artifact_id}.json"

    def _index_key(self) -> str:
        return "production/artifacts/index.json"

    def _revision_key(self, artifact_id: str, revision_id: str) -> str:
        return f"production/revisions/{artifact_id}/{revision_id}.json"

    def _intervention_key(self, intervention_id: str) -> str:
        return f"production/interventions/{intervention_id}.json"

    async def _update_index(self, artifact_id: str, campaign_id: str) -> None:
        index_data: Dict[str, str] = {}
        idx_key = self._index_key()
        if await self.storage.exists(idx_key):
            try:
                raw = await self.storage.download_bytes(idx_key)
                index_data = json.loads(raw.decode("utf-8"))
            except Exception:
                index_data = {}
        index_data[artifact_id] = campaign_id
        await self.storage.upload_bytes(
            json.dumps(index_data, indent=2).encode("utf-8"),
            idx_key,
            content_type="application/json",
        )

    async def save_artifact(self, artifact: ProductionArtifact) -> None:
        """Saves a production artifact immutably under its unique ID."""
        key = self._artifact_key(artifact.campaign_id, artifact.artifact_id)
        payload = artifact.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(payload, key, content_type="application/json")
        await self._update_index(artifact.artifact_id, artifact.campaign_id)
        logger.info(
            "Saved production artifact",
            artifact_id=artifact.artifact_id,
            campaign_id=artifact.campaign_id,
            review_status=artifact.review_status.value,
            storage_key=key,
        )

    async def get_artifact(self, artifact_id: str, campaign_id: Optional[str] = None) -> Optional[ProductionArtifact]:
        """Retrieves a specific production artifact by artifact_id."""
        target_campaign = campaign_id
        if not target_campaign:
            idx_key = self._index_key()
            if await self.storage.exists(idx_key):
                try:
                    raw = await self.storage.download_bytes(idx_key)
                    index_data = json.loads(raw.decode("utf-8"))
                    target_campaign = index_data.get(artifact_id)
                except Exception:
                    pass

        if not target_campaign:
            # Search all artifacts in storage prefix
            all_files = await self.storage.list_files("production/artifacts/")
            for item in all_files:
                k_str = item.storage_key if hasattr(item, "storage_key") else str(item)
                if k_str.endswith(f"/{artifact_id}.json"):
                    raw = await self.storage.download_bytes(k_str)
                    return ProductionArtifact.model_validate_json(raw.decode("utf-8"))
            return None

        key = self._artifact_key(target_campaign, artifact_id)
        if not await self.storage.exists(key):
            return None
        raw = await self.storage.download_bytes(key)
        return ProductionArtifact.model_validate_json(raw.decode("utf-8"))

    async def list_artifacts(
        self,
        campaign_id: Optional[str] = None,
        review_status: Optional[ReviewStatus] = None,
    ) -> List[ProductionArtifact]:
        """Lists production artifacts, optionally filtered by campaign or review status."""
        prefix = f"production/artifacts/{campaign_id}/" if campaign_id else "production/artifacts/"
        items = await self.storage.list_files(prefix)
        artifacts: List[ProductionArtifact] = []

        for item in items:
            k_str = item.storage_key if hasattr(item, "storage_key") else str(item)
            if k_str.endswith(".json") and not k_str.endswith("index.json"):
                try:
                    raw = await self.storage.download_bytes(k_str)
                    art = ProductionArtifact.model_validate_json(raw.decode("utf-8"))
                    if review_status and art.review_status != review_status:
                        continue
                    artifacts.append(art)
                except Exception as e:
                    logger.warning("Failed to deserialize production artifact", key=k_str, error=str(e))

        artifacts.sort(key=lambda a: a.created_at, reverse=True)
        return artifacts

    async def update_review_status(
        self,
        artifact_id: str,
        status: ReviewStatus,
        operator_id: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> Optional[ProductionArtifact]:
        """Updates human approval state on an artifact."""
        artifact = await self.get_artifact(artifact_id)
        if not artifact:
            return None

        update_dict: Dict[str, Any] = {
            "review_status": status,
            "updated_at": datetime.now(timezone.utc),
        }
        if feedback:
            update_dict["operator_feedback"] = feedback
        if status == ReviewStatus.APPROVED:
            update_dict["production_status"] = ProductionStatus.APPROVED
        elif status == ReviewStatus.REVISION_REQUIRED:
            update_dict["production_status"] = ProductionStatus.REVISION_REQUIRED

        updated = artifact.model_copy(update=update_dict)
        await self.save_artifact(updated)
        return updated

    async def save_revision(self, revision: RevisionRecord) -> None:
        """Persists an immutable revision record for an artifact rejection."""
        key = self._revision_key(revision.original_artifact_id, revision.revision_id)
        payload = revision.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(payload, key, content_type="application/json")
        logger.info(
            "Saved artifact revision record",
            original_artifact_id=revision.original_artifact_id,
            revision_id=revision.revision_id,
            revision_number=revision.revision_number,
        )

    async def list_revisions_for_artifact(self, original_artifact_id: str) -> List[RevisionRecord]:
        """Lists all revisions logged for an artifact in chronological order."""
        prefix = f"production/revisions/{original_artifact_id}/"
        items = await self.storage.list_files(prefix)
        revisions: List[RevisionRecord] = []
        for item in items:
            k_str = item.storage_key if hasattr(item, "storage_key") else str(item)
            if k_str.endswith(".json"):
                try:
                    raw = await self.storage.download_bytes(k_str)
                    rev = RevisionRecord.model_validate_json(raw.decode("utf-8"))
                    revisions.append(rev)
                except Exception:
                    pass
        revisions.sort(key=lambda r: r.revision_number)
        return revisions

    async def save_intervention(self, record: OperatorInterventionRecord) -> None:
        """Saves a human intervention / challenge escalation record."""
        key = self._intervention_key(record.intervention_id)
        payload = record.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(payload, key, content_type="application/json")
        logger.info(
            "Saved operator intervention record",
            intervention_id=record.intervention_id,
            campaign_id=record.campaign_id,
            checkpoint=record.checkpoint,
        )

    async def get_intervention(self, intervention_id: str) -> Optional[OperatorInterventionRecord]:
        """Retrieves an operator intervention record by ID."""
        key = self._intervention_key(intervention_id)
        if not await self.storage.exists(key):
            return None
        raw = await self.storage.download_bytes(key)
        return OperatorInterventionRecord.model_validate_json(raw.decode("utf-8"))

    async def list_pending_interventions(self, campaign_id: Optional[str] = None) -> List[OperatorInterventionRecord]:
        """Lists active pending human intervention requests."""
        items = await self.storage.list_files("production/interventions/")
        records: List[OperatorInterventionRecord] = []
        for item in items:
            k_str = item.storage_key if hasattr(item, "storage_key") else str(item)
            if k_str.endswith(".json"):
                try:
                    raw = await self.storage.download_bytes(k_str)
                    rec = OperatorInterventionRecord.model_validate_json(raw.decode("utf-8"))
                    if rec.status == "PENDING_OPERATOR":
                        if campaign_id and rec.campaign_id != campaign_id:
                            continue
                        records.append(rec)
                except Exception:
                    pass
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    async def resume_intervention(self, intervention_id: str) -> Optional[OperatorInterventionRecord]:
        """Transitions an intervention from PENDING_OPERATOR to RESUMED."""
        rec = await self.get_intervention(intervention_id)
        if not rec:
            return None
        updated = rec.model_copy(
            update={
                "status": "RESUMED",
                "resumed_at": datetime.now(timezone.utc),
            }
        )
        await self.save_intervention(updated)
        return updated
