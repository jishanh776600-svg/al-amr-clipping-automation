"""Campaign Source Registry for Extensible Marketplace Integrations."""

from typing import Dict, List, Optional
from clipping.agent.campaign.sources.base import CampaignSource
from clipping.agent.campaign.sources.whop import WhopCampaignSource


class CampaignSourceRegistry:
    """
    Registry of legitimate campaign discovery sources.
    Defaults to Whop as the authoritative primary campaign engine.
    """

    def __init__(self):
        self._sources: Dict[str, CampaignSource] = {}
        # Register Whop by default
        whop = WhopCampaignSource()
        self.register(whop)

    def register(self, source: CampaignSource) -> None:
        self._sources[source.source_id] = source

    def get_source(self, source_id: str) -> Optional[CampaignSource]:
        return self._sources.get(source_id)

    def get_primary_source(self) -> CampaignSource:
        for source in self._sources.values():
            if source.is_primary:
                return source
        # Fallback to first registered source or new Whop source
        if self._sources:
            return next(iter(self._sources.values()))
        whop = WhopCampaignSource()
        self.register(whop)
        return whop

    def list_sources(self) -> List[CampaignSource]:
        return list(self._sources.values())
