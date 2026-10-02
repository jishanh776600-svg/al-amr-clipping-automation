"""Whop Campaign State Machine and Durable Models (Step 3).

Defines lifecycle states, allowed state transitions, event models,
and the canonical CampaignRecord structure for the persistent ledger.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal state machine transition is attempted."""
    pass


class CampaignState(str, Enum):
    # Active discovery & validation states (actively used in Step 3)
    DISCOVERED = "DISCOVERED"
    VALIDATING = "VALIDATING"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"

    # Future processing states (defined for forward compatibility, not executed in Step 3)
    CLAIMING = "CLAIMING"
    CLAIMED = "CLAIMED"
    INGESTED = "INGESTED"
    RENDERING = "RENDERING"
    RENDER_READY = "RENDER_READY"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"

    # Failure / terminal states
    CLAIM_FAILED = "CLAIM_FAILED"
    INGEST_FAILED = "INGEST_FAILED"
    RENDER_FAILED = "RENDER_FAILED"
    INSUFFICIENT_VALID_CLIPS = "INSUFFICIENT_VALID_CLIPS"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    SUBMISSION_FAILED = "SUBMISSION_FAILED"


# Authoritative state transition map
ALLOWED_TRANSITIONS: Dict[CampaignState, Set[CampaignState]] = {
    CampaignState.DISCOVERED: {
        CampaignState.VALIDATING,
        CampaignState.REJECTED,
    },
    CampaignState.VALIDATING: {
        CampaignState.ELIGIBLE,
        CampaignState.REJECTED,
    },
    CampaignState.REJECTED: {
        # Can re-validate if metadata updates (e.g. payout increased, media added)
        CampaignState.VALIDATING,
    },
    CampaignState.ELIGIBLE: {
        CampaignState.VALIDATING,  # re-evaluation on rediscovery
        CampaignState.REJECTED,    # disqualified on rediscovery
        CampaignState.CLAIMING,
    },
    CampaignState.CLAIMING: {
        CampaignState.CLAIMED,
        CampaignState.CLAIM_FAILED,
    },
    CampaignState.CLAIMED: {
        CampaignState.INGESTED,
        CampaignState.INGEST_FAILED,
    },
    CampaignState.INGESTED: {
        CampaignState.RENDERING,
        CampaignState.RENDER_FAILED,
        CampaignState.INSUFFICIENT_VALID_CLIPS,
    },
    CampaignState.RENDERING: {
        CampaignState.RENDER_READY,
        CampaignState.RENDER_FAILED,
    },
    CampaignState.RENDER_READY: {
        CampaignState.AWAITING_APPROVAL,
        CampaignState.SUBMITTING,  # if autonomous mode approved
    },
    CampaignState.AWAITING_APPROVAL: {
        CampaignState.APPROVED,
        CampaignState.APPROVAL_REJECTED,
    },
    CampaignState.APPROVED: {
        CampaignState.SUBMITTING,
    },
    CampaignState.SUBMITTING: {
        CampaignState.SUBMITTED,
        CampaignState.SUBMISSION_FAILED,
    },
    # Failure states can transition to retries or re-evaluation
    CampaignState.CLAIM_FAILED: {CampaignState.CLAIMING, CampaignState.REJECTED},
    CampaignState.INGEST_FAILED: {CampaignState.INGESTED, CampaignState.REJECTED},
    CampaignState.RENDER_FAILED: {CampaignState.RENDERING, CampaignState.REJECTED},
    CampaignState.INSUFFICIENT_VALID_CLIPS: {CampaignState.INGESTED, CampaignState.REJECTED},
    CampaignState.APPROVAL_REJECTED: {CampaignState.REJECTED},
    CampaignState.SUBMISSION_FAILED: {CampaignState.SUBMITTING, CampaignState.REJECTED},
    CampaignState.SUBMITTED: set(),  # Terminal successful state
}


def validate_transition(current: CampaignState, target: CampaignState) -> None:
    """Validates that a transition from current to target state is legally permitted.
    
    If current == target, it is considered a no-op (safe, idempotent).
    Raises InvalidStateTransitionError on invalid transition.
    """
    if current == target:
        return

    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal state machine transition: cannot transition from '{current.value}' to '{target.value}'. "
            f"Allowed next states: {[s.value for s in allowed]}"
        )


@dataclass
class CampaignEvent:
    """Immutable audit trail event for campaign state changes and updates."""
    campaign_id: str
    previous_state: Optional[str]
    new_state: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str = ""
    source: str = "whop_discovery"
    metadata_json: str = "{}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CampaignRecord:
    """Authoritative durable record of a Whop campaign in the ledger."""
    campaign_id: str
    title: str
    campaign_url: str
    payout_raw: str = ""
    cpm: Optional[float] = None
    platforms: List[str] = field(default_factory=list)
    source_urls: List[str] = field(default_factory=list)
    guideline_urls: List[str] = field(default_factory=list)
    recommended_account: Optional[str] = None
    current_state: CampaignState = CampaignState.DISCOVERED
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Retry and error diagnostics
    last_error: Optional[str] = None
    retry_count: int = 0
    last_attempt_at: Optional[str] = None

    # Forward-compatible execution attributes (set in future steps)
    job_id: Optional[str] = None
    submission_url: Optional[str] = None
    submitted_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["current_state"] = self.current_state.value
        return d