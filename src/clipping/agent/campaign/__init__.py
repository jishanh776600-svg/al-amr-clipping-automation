"""Campaign Module."""

from clipping.agent.campaign.discovery import CampaignDiscoveryCapability
from clipping.agent.campaign.models import (
    AccountRequirements,
    CampaignPlatform,
    CampaignRecord,
    CampaignStatus,
    PostingRequirements,
)
from clipping.agent.campaign.repository import CampaignRepository

__all__ = [
    "AccountRequirements",
    "CampaignDiscoveryCapability",
    "CampaignPlatform",
    "CampaignRecord",
    "CampaignRepository",
    "CampaignStatus",
    "PostingRequirements",
]
