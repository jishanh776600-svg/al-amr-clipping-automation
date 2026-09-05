"""Base Contracts for Platform-Agnostic Publishing Adapters."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from clipping.agent.escalation import EscalationContext
from clipping.agent.publishing.models import CampaignSubmissionRecord, SubmissionStatus
from clipping.agent.vault.models import AccountPlatform


class PlatformPublishResult(BaseModel):
    """Normalized outcome returned by platform publishing adapters."""
    model_config = ConfigDict(frozen=True)

    success: bool
    platform_post_id: Optional[str] = None
    platform_url: Optional[str] = None
    status: SubmissionStatus
    error_message: Optional[str] = None
    failure_classification: Optional[str] = None
    is_retryable: bool = False
    escalation_required: bool = False
    escalation_context: Optional[EscalationContext] = None
    raw_response: Dict[str, Any] = Field(default_factory=dict)


class PlatformStatusResult(BaseModel):
    """Normalized status report returned by live platform reconciliation."""
    model_config = ConfigDict(frozen=True)

    post_id: str
    exists_on_platform: bool
    platform_status: SubmissionStatus
    privacy_status: Optional[str] = None
    view_count: Optional[int] = None
    scheduled_time: Optional[str] = None
    error_message: Optional[str] = None
    raw_details: Dict[str, Any] = Field(default_factory=dict)


class PlatformPublishingAdapter(ABC):
    """Abstract interface governing all social and video publishing adapters."""

    @property
    @abstractmethod
    def platform(self) -> AccountPlatform:
        """Target social platform."""
        pass

    @abstractmethod
    async def publish(
        self,
        submission: CampaignSubmissionRecord,
        media_path: str,
        credentials: Dict[str, Any],
    ) -> PlatformPublishResult:
        """Publishes or schedules a video clip on the target platform."""
        pass

    @abstractmethod
    async def reconcile_status(
        self,
        platform_post_id: str,
        credentials: Dict[str, Any],
    ) -> PlatformStatusResult:
        """Queries the live platform API or page to inspect the actual post state."""
        pass
