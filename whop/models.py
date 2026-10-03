"""Whop Campaign State Machine and Durable Models (Step 3).

Defines lifecycle states, allowed state transitions, event models,
and the canonical CampaignRecord structure for the persistent ledger.
"""

from __future__ import annotations

import hashlib
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
    RENDER_WARN = "RENDER_WARN"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"

    # Failure / terminal states
    CLAIM_FAILED = "CLAIM_FAILED"
    INGEST_FAILED = "INGEST_FAILED"
    RENDER_FAILED = "RENDER_FAILED"
    INSUFFICIENT_VALID_CLIPS = "INSUFFICIENT_VALID_CLIPS"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    SUBMISSION_FAILED = "SUBMISSION_FAILED"
    SUBMISSION_BLOCKED = "SUBMISSION_BLOCKED"


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
        CampaignState.INGESTED,
        CampaignState.INGEST_FAILED,
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
        CampaignState.RENDER_WARN,
        CampaignState.RENDER_FAILED,
        CampaignState.INSUFFICIENT_VALID_CLIPS,
    },
    CampaignState.RENDER_READY: {
        CampaignState.AWAITING_APPROVAL,
        CampaignState.APPROVED,    # if autonomous mode approved
        CampaignState.SUBMITTING,  # if autonomous mode approved
    },
    CampaignState.RENDER_WARN: {
        CampaignState.AWAITING_APPROVAL,
        CampaignState.APPROVED,    # if autonomous mode approved
        CampaignState.SUBMITTING,
        CampaignState.RENDERING,
        CampaignState.REJECTED,
    },
    CampaignState.AWAITING_APPROVAL: {
        CampaignState.APPROVED,
        CampaignState.APPROVAL_REJECTED,
        CampaignState.CHANGES_REQUESTED,
    },
    CampaignState.CHANGES_REQUESTED: {
        CampaignState.RENDERING,
        CampaignState.REJECTED,
    },
    CampaignState.APPROVED: {
        CampaignState.SUBMITTING,
        CampaignState.SUBMISSION_BLOCKED,
    },
    CampaignState.SUBMITTING: {
        CampaignState.SUBMITTED,
        CampaignState.SUBMISSION_FAILED,
        CampaignState.SUBMISSION_BLOCKED,
    },
    # Failure states can transition to retries or re-evaluation
    CampaignState.CLAIM_FAILED: {CampaignState.CLAIMING, CampaignState.REJECTED},
    CampaignState.INGEST_FAILED: {CampaignState.INGESTED, CampaignState.REJECTED},
    CampaignState.RENDER_FAILED: {CampaignState.RENDERING, CampaignState.REJECTED},
    CampaignState.INSUFFICIENT_VALID_CLIPS: {CampaignState.INGESTED, CampaignState.RENDERING, CampaignState.REJECTED},
    CampaignState.APPROVAL_REJECTED: {CampaignState.REJECTED},
    CampaignState.SUBMISSION_FAILED: {CampaignState.SUBMITTING, CampaignState.REJECTED},
    CampaignState.SUBMISSION_BLOCKED: {CampaignState.AWAITING_APPROVAL, CampaignState.REJECTED},
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


@dataclass
class WhopAutoClipJobRecord:
    """Authoritative durable record linking a Whop campaign to an AutoClip job."""
    campaign_id: str
    guideline_hash: str
    autoclip_job_id: str
    idempotency_key: str
    status: str
    request_hash: str
    source_hash: str
    id: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_error: Optional[str] = None
    metadata_json: str = "{}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ==============================================================================
# Step 4: CampaignBrief, Guidelines & Rule Provenance Models
# ==============================================================================

class RuleCategory(str, Enum):
    CONTENT = "CONTENT"
    SOURCE = "SOURCE"
    DURATION = "DURATION"
    EDITING = "EDITING"
    CAPTIONS = "CAPTIONS"
    SUBTITLES = "SUBTITLES"
    VISUAL = "VISUAL"
    AUDIO = "AUDIO"
    BGM = "BGM"
    SFX = "SFX"
    BRANDING = "BRANDING"
    CTA = "CTA"
    SEO = "SEO"
    HASHTAGS = "HASHTAGS"
    PUBLISHING = "PUBLISHING"
    SUBMISSION = "SUBMISSION"
    COPYRIGHT = "COPYRIGHT"
    OTHER = "OTHER"


class ParsingStatus(str, Enum):
    PARSED = "PARSED"
    PARTIAL = "PARTIAL"
    GUIDELINES_UNAVAILABLE = "GUIDELINES_UNAVAILABLE"
    FAILED = "FAILED"


@dataclass
class CampaignRule:
    """An individual atomic rule with strict provenance and classification."""
    rule_id: str
    category: RuleCategory
    text: str
    normalized_value: Any = None
    mandatory: bool = False
    prohibited: bool = False
    platform: str = "all"  # "all", "youtube", "instagram", "tiktok"
    source_reference: str = ""
    source_excerpt: str = ""
    confidence: str = "explicit"  # "explicit", "inferred"
    status: str = "ACTIVE"  # "ACTIVE", "INTERPRETATION_REQUIRED", "OPERATIONAL"
    is_operational: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> CampaignRule:
        cat = d.get("category", RuleCategory.OTHER.value)
        if isinstance(cat, str):
            cat = RuleCategory(cat)
        return cls(
            rule_id=d["rule_id"],
            category=cat,
            text=d.get("text", ""),
            normalized_value=d.get("normalized_value"),
            mandatory=bool(d.get("mandatory", False)),
            prohibited=bool(d.get("prohibited", False)),
            platform=d.get("platform", "all"),
            source_reference=d.get("source_reference", ""),
            source_excerpt=d.get("source_excerpt", ""),
            confidence=d.get("confidence", "explicit"),
            status=d.get("status", "ACTIVE"),
            is_operational=bool(d.get("is_operational", False)),
        )


@dataclass
class WhopCampaignBrief:
    """Canonical structured representation of campaign compliance requirements."""
    # Identity
    campaign_id: str
    title: str
    campaign_url: str
    source_platform: str = "whop"
    payout_raw: str = ""
    cpm: Optional[float] = None
    supported_platforms: List[str] = field(default_factory=list)

    # Guideline Provenance
    guideline_source_type: str = "detail_page"  # "detail_page", "pdf", "dropbox", "google_drive", "external_doc", "none"
    guideline_source_reference: str = ""
    guideline_hash: str = ""
    guideline_version: Optional[str] = None
    parsed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_guideline_text: str = ""
    parser_version: str = "1.0.0"
    parsing_status: ParsingStatus = ParsingStatus.PARSED

    # Content Requirements & Technical Constraints
    duration_min_s: Optional[float] = None
    duration_max_s: Optional[float] = None
    duration_preferred_s: Optional[float] = None
    duration_exact_s: Optional[float] = None
    required_topics: List[str] = field(default_factory=list)
    allowed_sources: List[str] = field(default_factory=list)
    forbidden_sources: List[str] = field(default_factory=list)
    banned_words: List[str] = field(default_factory=list)
    banned_topics: List[str] = field(default_factory=list)
    visual_instructions: List[str] = field(default_factory=list)
    caption_preset: str = "bold_pop"
    caption_rules: List[str] = field(default_factory=list)
    cta_wording: str = ""
    cta_placement: str = ""
    cta_instructions: List[str] = field(default_factory=list)
    bgm_rules: List[str] = field(default_factory=list)
    sfx_rules: List[str] = field(default_factory=list)
    branding_rules: List[str] = field(default_factory=list)
    logo_watermark_required: bool = False
    hook_instructions: List[str] = field(default_factory=list)

    # Publishing & Platform-Specific Requirements
    youtube_requirements: List[str] = field(default_factory=list)
    instagram_requirements: List[str] = field(default_factory=list)
    tiktok_requirements: List[str] = field(default_factory=list)
    title_requirements: List[str] = field(default_factory=list)
    description_guidelines: List[str] = field(default_factory=list)
    hashtags: List[str] = field(default_factory=list)
    required_mentions: List[str] = field(default_factory=list)
    link_in_bio: Optional[str] = None
    approval_gate_required: bool = False

    # Operational Instructions (explicitly separated from video-editing rules)
    operational_instructions: List[str] = field(default_factory=list)

    # Atomic Rules Collection
    rules: List[CampaignRule] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["parsing_status"] = self.parsing_status.value
        d["rules"] = [r.to_dict() for r in self.rules]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> WhopCampaignBrief:
        p_status = d.get("parsing_status", ParsingStatus.PARSED.value)
        if isinstance(p_status, str):
            p_status = ParsingStatus(p_status)

        rules = [CampaignRule.from_dict(r) for r in d.get("rules", [])]

        return cls(
            campaign_id=d["campaign_id"],
            title=d["title"],
            campaign_url=d.get("campaign_url", ""),
            source_platform=d.get("source_platform", "whop"),
            payout_raw=d.get("payout_raw", ""),
            cpm=d.get("cpm"),
            supported_platforms=d.get("supported_platforms", []),
            guideline_source_type=d.get("guideline_source_type", "detail_page"),
            guideline_source_reference=d.get("guideline_source_reference", ""),
            guideline_hash=d.get("guideline_hash", ""),
            guideline_version=d.get("guideline_version"),
            parsed_at=d.get("parsed_at") or datetime.now(timezone.utc).isoformat(),
            raw_guideline_text=d.get("raw_guideline_text", ""),
            parser_version=d.get("parser_version", "1.0.0"),
            parsing_status=p_status,
            duration_min_s=d.get("duration_min_s"),
            duration_max_s=d.get("duration_max_s"),
            duration_preferred_s=d.get("duration_preferred_s"),
            duration_exact_s=d.get("duration_exact_s"),
            required_topics=d.get("required_topics", []),
            allowed_sources=d.get("allowed_sources", []),
            forbidden_sources=d.get("forbidden_sources", []),
            banned_words=d.get("banned_words", []),
            banned_topics=d.get("banned_topics", []),
            visual_instructions=d.get("visual_instructions", []),
            caption_preset=d.get("caption_preset", "bold_pop"),
            caption_rules=d.get("caption_rules", []),
            cta_wording=d.get("cta_wording", ""),
            cta_placement=d.get("cta_placement", ""),
            cta_instructions=d.get("cta_instructions", []),
            bgm_rules=d.get("bgm_rules", []),
            sfx_rules=d.get("sfx_rules", []),
            branding_rules=d.get("branding_rules", []),
            logo_watermark_required=bool(d.get("logo_watermark_required", False)),
            hook_instructions=d.get("hook_instructions", []),
            youtube_requirements=d.get("youtube_requirements", []),
            instagram_requirements=d.get("instagram_requirements", []),
            tiktok_requirements=d.get("tiktok_requirements", []),
            title_requirements=d.get("title_requirements", []),
            description_guidelines=d.get("description_guidelines", []),
            hashtags=d.get("hashtags", []),
            required_mentions=d.get("required_mentions", []),
            link_in_bio=d.get("link_in_bio"),
            approval_gate_required=bool(d.get("approval_gate_required", False)),
            operational_instructions=d.get("operational_instructions", []),
            rules=rules,
        )

    def to_autoclip_brief(self) -> Any:
        """Bridges to AutoClip canonical CampaignBrief for seamless downstream clipping."""
        try:
            from backend.autoclip.campaign.models import CampaignBrief
        except ImportError:
            # Fallback if autoclip package not installed in global namespace
            from autoclip.campaign.models import CampaignBrief

        min_dur = self.duration_min_s if self.duration_min_s and self.duration_min_s > 0 else 20.0
        max_dur = self.duration_max_s if self.duration_max_s and self.duration_max_s > 0 else 90.0
        if min_dur > max_dur:
            min_dur, max_dur = max_dur, min_dur

        mandatory_rules = [r.text for r in self.rules if getattr(r, "mandatory", False) and getattr(r, "text", "")]
        preference_rules = [r.text for r in self.rules if not getattr(r, "mandatory", False) and getattr(r, "text", "")]

        return CampaignBrief(
            campaign_id=self.campaign_id,
            name=self.title[:80],
            description=f"Whop campaign: {self.title}",
            topic_context=self.title,
            required_topics=self.required_topics[:10],
            banned_words=self.banned_words[:20],
            banned_topics=self.banned_topics[:20],
            minimum_duration=float(min_dur),
            maximum_duration=float(max_dur),
            preferred_duration=float(self.duration_preferred_s) if self.duration_preferred_s else None,
            caption_preset=self.caption_preset or "bold_pop",
            hashtags=self.hashtags[:15],
            title_patterns=self.title_requirements[:5],
            description_guidelines=self.description_guidelines[:5],
            required_mentions=self.required_mentions[:10],
            cta_text=self.cta_wording,
            cta_instructions=self.cta_instructions,
            branding_rules=self.branding_rules,
            mandatory_rules=mandatory_rules,
            preference_rules=preference_rules,
        )


def validate_campaign_brief(brief: WhopCampaignBrief) -> List[str]:
    """Strictly validates a WhopCampaignBrief.
    
    Returns a list of error strings. Empty list indicates valid brief.
    Preserves legitimate null/unknown values (unknown is NOT invalid).
    """
    errors: List[str] = []

    if not brief.campaign_id or not brief.campaign_id.strip():
        errors.append("Validation Error: Missing campaign_id")

    if not brief.title or not brief.title.strip():
        errors.append("Validation Error: Missing title")

    if not isinstance(brief.parsing_status, ParsingStatus):
        errors.append(f"Validation Error: Invalid parsing_status '{brief.parsing_status}'")

    if brief.parsing_status != ParsingStatus.GUIDELINES_UNAVAILABLE and not brief.guideline_hash:
        errors.append("Validation Error: Missing guideline_hash for parsed/partial guidelines")

    # Contradictory duration
    if (
        brief.duration_min_s is not None
        and brief.duration_max_s is not None
        and brief.duration_min_s > brief.duration_max_s
    ):
        errors.append(
            f"Validation Error: Contradictory duration constraints (min {brief.duration_min_s}s > max {brief.duration_max_s}s)"
        )

    # Rule validations
    seen_rule_ids: Set[str] = set()
    for idx, rule in enumerate(brief.rules):
        if not rule.rule_id:
            errors.append(f"Validation Error: Rule at index {idx} has empty rule_id")
        elif rule.rule_id in seen_rule_ids:
            errors.append(f"Validation Error: Duplicate rule_id '{rule.rule_id}' detected")
        seen_rule_ids.add(rule.rule_id)

        if not rule.text or not rule.text.strip():
            errors.append(f"Validation Error: Rule '{rule.rule_id}' has empty text")

        if not rule.source_reference:
            errors.append(f"Validation Error: Rule '{rule.rule_id}' is missing source_reference provenance")

        if rule.platform not in ("all", "youtube", "instagram", "tiktok"):
            errors.append(f"Validation Error: Rule '{rule.rule_id}' has invalid platform scope '{rule.platform}'")

    return errors


# ==============================================================================
# Step 6: Production Render & Quality Verification Models
# ==============================================================================

class RuleComplianceStatus(str, Enum):
    """Categorical compliance status for each CampaignBrief rule."""
    SUPPORTED_AND_SATISFIED = "SUPPORTED_AND_SATISFIED"
    SUPPORTED_AND_FAILED = "SUPPORTED_AND_FAILED"
    UNSUPPORTED_REQUIRES_REVIEW = "UNSUPPORTED_REQUIRES_REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class RuleComplianceResult:
    """Evaluation outcome of a single guideline rule against a candidate clip or campaign."""
    rule_id: str
    rule_text: str
    category: str
    mandatory: bool
    status: RuleComplianceStatus
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class ClipTechnicalQAResult:
    """Comprehensive technical video/audio/codec QA result for a rendered MP4 clip."""
    clip_id: str
    duration_s: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    channels: int = 2
    sample_rate: int = 48000
    mean_volume_db: float = -14.0
    true_peak_db: float = -1.5
    av_sync_diff_s: float = 0.0
    decode_ok: bool = True
    broll_coverage_pct: float = 0.0
    longest_a_roll_gap_s: float = 0.0
    file_size_bytes: int = 0
    warnings: List[str] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)
    is_valid: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ClipQARecord:
    """Authoritative evaluation record for a single clip candidate."""
    clip_id: str
    candidate_index: int
    technical_qa: ClipTechnicalQAResult
    compliance_results: List[RuleComplianceResult] = field(default_factory=list)
    artifact_path: str = ""
    drive_file_id: Optional[str] = None
    artifact_url: Optional[str] = None
    is_durable: bool = False
    is_distinct: bool = True
    quality_score: float = 100.0
    is_valid: bool = True
    rejection_summary: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "candidate_index": self.candidate_index,
            "technical_qa": self.technical_qa.to_dict(),
            "compliance_results": [r.to_dict() for r in self.compliance_results],
            "artifact_path": self.artifact_path,
            "drive_file_id": self.drive_file_id,
            "artifact_url": self.artifact_url,
            "is_durable": self.is_durable,
            "is_distinct": self.is_distinct,
            "quality_score": self.quality_score,
            "is_valid": self.is_valid,
            "rejection_summary": self.rejection_summary,
            "metadata": self.metadata,
        }


@dataclass
class WhopJobQAReport:
    """Authoritative durable record of a complete Render & Quality Verification run."""
    campaign_id: str
    guideline_hash: str
    autoclip_job_id: str
    artifact_hash: str
    qa_status: str  # RENDER_PASS, RENDER_WARN, RENDER_FAILED, INSUFFICIENT_VALID_CLIPS
    overall_quality_score: float
    valid_clips_count: int
    total_clips_evaluated: int
    clips: List[ClipQARecord] = field(default_factory=list)
    compliance_summary: Dict[str, int] = field(default_factory=dict)
    unsupported_rules: List[RuleComplianceResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "campaign_id": self.campaign_id,
            "guideline_hash": self.guideline_hash,
            "autoclip_job_id": self.autoclip_job_id,
            "artifact_hash": self.artifact_hash,
            "qa_status": self.qa_status,
            "overall_quality_score": self.overall_quality_score,
            "valid_clips_count": self.valid_clips_count,
            "total_clips_evaluated": self.total_clips_evaluated,
            "clips": [c.to_dict() for c in self.clips],
            "compliance_summary": self.compliance_summary,
            "unsupported_rules": [r.to_dict() for r in self.unsupported_rules],
            "warnings": self.warnings,
            "failures": self.failures,
            "created_at": self.created_at,
        }


# ==============================================================================
# Step 7: Telegram Human Approval Gate Models
# ==============================================================================

@dataclass
class WhopReviewSession:
    """Authoritative durable record of a Telegram Human Review session."""
    review_session_id: str
    campaign_id: str
    guideline_hash: str
    autoclip_job_id: str
    artifact_hash: str
    idempotency_key: str
    review_state: str = "PENDING"  # PENDING, APPROVED, CHANGES_REQUESTED, REJECTED
    chat_id: str = ""
    message_ids: Dict[str, int] = field(default_factory=dict)
    telegram_file_ids: Dict[str, str] = field(default_factory=dict)
    clip_ids: List[str] = field(default_factory=list)
    clip_order: List[str] = field(default_factory=list)
    reviewer_id: Optional[str] = None
    reviewer_username: Optional[str] = None
    decision: Optional[str] = None
    decision_note: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "review_session_id": self.review_session_id,
            "campaign_id": self.campaign_id,
            "guideline_hash": self.guideline_hash,
            "autoclip_job_id": self.autoclip_job_id,
            "artifact_hash": self.artifact_hash,
            "idempotency_key": self.idempotency_key,
            "review_state": self.review_state,
            "chat_id": self.chat_id,
            "message_ids": self.message_ids,
            "telegram_file_ids": self.telegram_file_ids,
            "clip_ids": self.clip_ids,
            "clip_order": self.clip_order,
            "reviewer_id": self.reviewer_id,
            "reviewer_username": self.reviewer_username,
            "decision": self.decision,
            "decision_note": self.decision_note,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ==============================================================================
# Step 7.4: Video Source Capability Classification & Probe Models
# ==============================================================================

class SourceCapability(str, Enum):
    """Deterministic capability classification for campaign media sources."""
    DIRECT_MEDIA = "DIRECT_MEDIA"
    GOOGLE_DRIVE = "GOOGLE_DRIVE"
    GOOGLE_DRIVE_INTEGRATED = "GOOGLE_DRIVE_INTEGRATED"
    DROPBOX_PUBLIC = "DROPBOX_PUBLIC"
    PUBLIC_CDN = "PUBLIC_CDN"
    PUBLIC_S3 = "PUBLIC_S3"
    PUBLIC_FILE_HOST = "PUBLIC_FILE_HOST"
    PUBLIC_YT_DLP_SUPPORTED = "PUBLIC_YT_DLP_SUPPORTED"
    PUBLIC_YOUTUBE_RESTRICTED = "PUBLIC_YOUTUBE_RESTRICTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    COOKIE_REQUIRED = "COOKIE_REQUIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    PRIVATE = "PRIVATE"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class SourceTier(int, Enum):
    """Deterministic selection priority tier for autonomous cloud execution."""
    TIER_0_DIRECT_CDN_S3 = 0        # Direct media, CDN, public S3 (highest speed, zero anti-bot)
    TIER_1_GOOGLE_DRIVE = 1         # Google Drive integrated service authentication
    TIER_2_PUBLIC_FILE_HOST = 2     # Other verified public no-login file hosts
    TIER_3_DROPBOX_PUBLIC = 3       # Public direct Dropbox media
    TIER_4_YOUTUBE_RESTRICTED = 4   # YouTube (operationally restricted on cloud workers)
    TIER_5_AUTH_BLOCKED = 5         # Login/private/auth required (ineligible)


@dataclass
class SourceProbeResult:
    """Safe read-only source probe inspection and media validation result."""
    url: str
    final_url: str = ""
    domain: str = ""
    capability: SourceCapability = SourceCapability.UNKNOWN
    tier: SourceTier = SourceTier.TIER_5_AUTH_BLOCKED
    is_supported_no_login: bool = False
    requires_login: bool = False
    requires_integrated_auth: bool = False
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    content_length: Optional[int] = None
    supports_range: bool = False
    is_valid_media: bool = False
    media_format: Optional[str] = None
    rejection_reason: Optional[str] = None
    probe_latency_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "domain": self.domain,
            "capability": self.capability.value,
            "tier": self.tier.value,
            "is_supported_no_login": self.is_supported_no_login,
            "requires_login": self.requires_login,
            "requires_integrated_auth": self.requires_integrated_auth,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "content_length": self.content_length,
            "supports_range": self.supports_range,
            "is_valid_media": self.is_valid_media,
            "media_format": self.media_format,
            "rejection_reason": self.rejection_reason,
            "probe_latency_ms": self.probe_latency_ms,
            "details": self.details,
        }


# ==============================================================================
# Step 8: Whop Submission Models & Data Structures
# ==============================================================================

class SubmissionState(str, Enum):
    PENDING = "PENDING"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    SUBMISSION_FAILED = "SUBMISSION_FAILED"
    SUBMISSION_BLOCKED = "SUBMISSION_BLOCKED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


@dataclass
class WhopSubmissionClipRef:
    """Canonical clip reference within an approved submission payload."""
    clip_id: str
    drive_file_id: str
    duration_s: float
    width: int
    height: int
    quality_score: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WhopSubmissionPayload:
    """Canonical deterministic submission payload for Whop."""
    campaign_id: str
    guideline_hash: str
    review_session_id: str
    destination: str
    clips: List[WhopSubmissionClipRef]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "guideline_hash": self.guideline_hash,
            "review_session_id": self.review_session_id,
            "destination": self.destination,
            "clips": [c.to_dict() for c in self.clips],
            "metadata": self.metadata,
        }

    def compute_hash(self) -> str:
        """Deterministic SHA-256 fingerprint of the canonical submission payload."""
        canonical_json = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


@dataclass
class WhopSubmissionRecord:
    """Authoritative durable record of a Whop submission in the ledger."""
    submission_id: str
    campaign_id: str
    guideline_hash: str
    review_session_id: str
    idempotency_key: str
    approval_event_id: Optional[int] = None
    approved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    submission_state: str = "PENDING"
    clip_ids: List[str] = field(default_factory=list)
    drive_file_ids: List[str] = field(default_factory=list)
    destination: str = "whop"
    whop_submission_id: Optional[str] = None
    attempt_count: int = 0
    last_attempt_at: Optional[str] = None
    error_classification: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "submission_id": self.submission_id,
            "campaign_id": self.campaign_id,
            "guideline_hash": self.guideline_hash,
            "review_session_id": self.review_session_id,
            "idempotency_key": self.idempotency_key,
            "approval_event_id": self.approval_event_id,
            "approved_at": self.approved_at,
            "submission_state": self.submission_state,
            "clip_ids": self.clip_ids,
            "drive_file_ids": self.drive_file_ids,
            "destination": self.destination,
            "whop_submission_id": self.whop_submission_id,
            "attempt_count": self.attempt_count,
            "last_attempt_at": self.last_attempt_at,
            "error_classification": self.error_classification,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ==============================================================================
# Autonomous Zero-Tolerance SEO Models
# ==============================================================================

@dataclass
class WhopSEOClipMetadata:
    """Deterministic, verified SEO metadata package for a single clip across platforms."""
    clip_id: str
    drive_file_id: str
    youtube_title: str
    youtube_description: str
    youtube_tags: List[str]
    instagram_caption: str
    instagram_hashtags: List[str]
    instagram_mentions: List[str]
    tiktok_caption: str
    tiktok_hashtags: List[str]
    tiktok_mentions: List[str]
    cta: str = ""
    compliance_score: float = 100.0
    is_compliant: bool = True
    matched_phrases: List[str] = field(default_factory=list)
    matched_mentions: List[str] = field(default_factory=list)
    matched_hashtags: List[str] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WhopSEOPackage:
    """Full campaign-level verified SEO package binding all clips to the campaign brief."""
    campaign_id: str
    guideline_hash: str
    clips_metadata: List[WhopSEOClipMetadata]
    total_clips: int
    all_compliant: bool = True
    verified_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "guideline_hash": self.guideline_hash,
            "clips_metadata": [m.to_dict() for m in self.clips_metadata],
            "total_clips": self.total_clips,
            "all_compliant": self.all_compliant,
            "verified_at": self.verified_at,
        }


@dataclass
class WhopJoinRecord:
    """Authoritative durable record tracking a genuine Whop campaign join mutation."""
    campaign_id: str
    campaign_name: str
    campaign_url: str
    account_identity: str
    guideline_hash: Optional[str] = None
    joined_at: Optional[str] = None
    join_attempt_count: int = 0
    join_state: str = "PENDING"
    membership_verified: bool = False
    safe_evidence_references: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_error: Optional[str] = None
    metadata_json: str = "{}"
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)