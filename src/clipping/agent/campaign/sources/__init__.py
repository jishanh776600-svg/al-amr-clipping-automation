"""Campaign Discovery Sources Package."""

from clipping.agent.campaign.sources.base import CampaignSource
from clipping.agent.campaign.sources.whop import WhopCampaignSource
from clipping.agent.campaign.sources.registry import CampaignSourceRegistry

__all__ = ["CampaignSource", "WhopCampaignSource", "CampaignSourceRegistry"]
