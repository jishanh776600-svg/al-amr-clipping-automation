"""Abstract Base Class for Video Ingestion."""

from abc import ABC, abstractmethod
from clipping.contracts.perception import SourceVideoMetadata
from clipping.ingestion.source import SourceReference
from clipping.storage.base import StorageDriver


class VideoIngestor(ABC):
    """Abstract interface for ingesting remote video assets into canonical storage."""

    @abstractmethod
    async def ingest(
        self,
        source_ref: SourceReference,
        storage_driver: StorageDriver,
        source_video_id: str,
        force_reingest: bool = False
    ) -> SourceVideoMetadata:
        """
        Ingests source video, persists master asset to storage vault,
        and returns SourceVideoMetadata.
        Operation is idempotent unless force_reingest is True.
        """
        pass

    @abstractmethod
    async def extract_metadata(self, source_ref: SourceReference) -> SourceVideoMetadata:
        """Fetches metadata without downloading the full video stream."""
        pass
