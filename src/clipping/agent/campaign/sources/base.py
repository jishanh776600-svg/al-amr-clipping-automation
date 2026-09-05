"""Extensible Campaign Source Abstract Base Class."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class CampaignSource(ABC):
    """
    Abstract interface for campaign discovery sources.
    Allows Whop and future legitimate creator marketplaces to be integrated
    without altering core campaign evaluation, ranking, or clipping engines.
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Unique machine identifier for the source (e.g. 'whop')."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable display name for the source."""
        pass

    @property
    def is_primary(self) -> bool:
        """Indicates if this source is the default primary discovery engine."""
        return False

    @abstractmethod
    async def discover(
        self,
        query: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = 50,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Discovers active clipping campaigns from the source.
        Returns a list of raw campaign brief dictionaries ready for normalization.
        """
        pass

    @abstractmethod
    async def fetch_campaign_detail(
        self,
        campaign_ref: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves detailed brief, updated terms, or current status for a specific campaign reference.
        """
        pass
