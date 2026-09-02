"""Abstract Base Classes for Campaign Document Parsing."""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from clipping.contracts.campaign import CampaignSpec, BoundingBox
from clipping.storage.base import StorageDriver


class ExtractedBlock(BaseModel):
    """Raw extracted text block with page and bounding box provenance."""
    model_config = ConfigDict(frozen=True)

    text: str
    page_no: int = Field(ge=1)
    bbox: Optional[BoundingBox] = None
    is_heading: bool = False
    heading_level: Optional[int] = None


class ExtractedTable(BaseModel):
    """Extracted table representation with cell coordinates."""
    model_config = ConfigDict(frozen=True)

    page_no: int = Field(ge=1)
    bbox: Optional[BoundingBox] = None
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)


class DocumentExtractionResult(BaseModel):
    """Intermediate structured representation of a parsed PDF."""
    model_config = ConfigDict(frozen=True)

    title: Optional[str] = None
    num_pages: int = Field(ge=1)
    blocks: List[ExtractedBlock] = Field(default_factory=list)
    tables: List[ExtractedTable] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CampaignDocumentParser(ABC):
    """Abstract interface for extracting CampaignSpec from Campaign PDFs."""

    @abstractmethod
    async def parse_bytes(self, pdf_bytes: bytes, campaign_id: str) -> CampaignSpec:
        """Parses raw PDF bytes into a validated CampaignSpec."""
        pass

    @abstractmethod
    async def parse_from_storage(
        self,
        storage_driver: StorageDriver,
        storage_key: str,
        campaign_id: str
    ) -> CampaignSpec:
        """Retrieves a PDF from the storage vault and parses it."""
        pass
