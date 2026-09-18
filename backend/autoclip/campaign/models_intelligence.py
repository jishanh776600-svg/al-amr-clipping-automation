"""Campaign Intelligence Data Models for AL AMR.

Defines the normalized data structures for multi-document campaign ingestion,
provenance tracking, rule confidence (explicit vs inferred), conflict detection,
and unified campaign specifications.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar

from .models import CampaignBrief, generate_campaign_id

T = TypeVar("T")


def utcnow_str() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RequirementItem(Generic[T]):
    """An individual extracted requirement with confidence and source provenance."""

    value: T
    confidence: Literal["explicit", "inferred"] = "explicit"
    source_doc_id: str = ""
    source_filename: str = ""
    source_type: str = ""  # pdf, docx, google_drive, google_docs, campaign_url
    snippet: str = ""
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "confidence": self.confidence,
            "source_doc_id": self.source_doc_id,
            "source_filename": self.source_filename,
            "source_type": self.source_type,
            "snippet": self.snippet,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RequirementItem[Any]:
        return cls(
            value=d.get("value"),
            confidence=d.get("confidence", "explicit"),
            source_doc_id=d.get("source_doc_id", ""),
            source_filename=d.get("source_filename", ""),
            source_type=d.get("source_type", ""),
            snippet=d.get("snippet", ""),
            weight=float(d.get("weight", 1.0)),
        )


@dataclass
class IngestedDocument:
    """Detailed provenance and extraction status for a single campaign document."""

    doc_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source_type: str = "pdf"  # pdf, docx, google_drive, google_docs, campaign_url
    filename: str = ""
    source_url: str | None = None
    sha256: str | None = None
    size_bytes: int = 0
    word_count: int = 0
    char_count: int = 0
    status: Literal["extracted", "partial", "failed"] = "extracted"
    error: str | None = None
    warning: str | None = None
    extracted_at: str = field(default_factory=utcnow_str)
    raw_text: str = ""

    def to_dict(self, include_raw_text: bool = False) -> dict[str, Any]:
        res: dict[str, Any] = {
            "doc_id": self.doc_id,
            "source_type": self.source_type,
            "filename": self.filename,
            "source_url": self.source_url,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "word_count": self.word_count,
            "char_count": self.char_count,
            "status": self.status,
            "error": self.error,
            "warning": self.warning,
            "extracted_at": self.extracted_at,
        }
        if include_raw_text:
            res["raw_text"] = self.raw_text
        return res

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IngestedDocument:
        return cls(
            doc_id=d.get("doc_id") or uuid.uuid4().hex[:12],
            source_type=d.get("source_type", "pdf"),
            filename=d.get("filename", ""),
            source_url=d.get("source_url"),
            sha256=d.get("sha256"),
            size_bytes=int(d.get("size_bytes", 0)),
            word_count=int(d.get("word_count", 0)),
            char_count=int(d.get("char_count", 0)),
            status=d.get("status", "extracted"),
            error=d.get("error"),
            warning=d.get("warning"),
            extracted_at=d.get("extracted_at") or utcnow_str(),
            raw_text=d.get("raw_text", ""),
        )


@dataclass
class CampaignConflict:
    """Explicit representation of conflicting rules across multiple campaign documents."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    rule_category: str = "duration"  # duration, aspect_ratio, topics, banned_terms, cta, platform
    severity: Literal["critical", "warning"] = "critical"
    document_a: dict[str, Any] = field(default_factory=dict)
    document_b: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    resolution_status: Literal["unresolved", "superseded", "resolved"] = "unresolved"
    resolution_notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_category": self.rule_category,
            "severity": self.severity,
            "document_a": self.document_a,
            "document_b": self.document_b,
            "description": self.description,
            "resolution_status": self.resolution_status,
            "resolution_notes": self.resolution_notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CampaignConflict:
        return cls(
            id=d.get("id") or uuid.uuid4().hex[:8],
            rule_category=d.get("rule_category", "duration"),
            severity=d.get("severity", "critical"),
            document_a=d.get("document_a", {}),
            document_b=d.get("document_b", {}),
            description=d.get("description", ""),
            resolution_status=d.get("resolution_status", "unresolved"),
            resolution_notes=d.get("resolution_notes"),
        )


@dataclass
class CampaignSpecification:
    """Normalized, multi-document Campaign Specification for AL AMR Clipping.

    Aggregates requirements across all provided documents and campaign URLs
    without silently dropping documents or hallucinating rules.
    """

    campaign_id: str = field(default_factory=generate_campaign_id)
    title: str = "Normalized Campaign"
    description: str = ""
    objective: str = ""
    target_audience: str = ""
    created_at: str = field(default_factory=utcnow_str)

    # Ingested materials
    documents: list[IngestedDocument] = field(default_factory=list)
    campaign_url: str | None = None

    # Topics & Themes
    desired_topics: list[RequirementItem[str]] = field(default_factory=list)
    preferred_speakers: list[RequirementItem[str]] = field(default_factory=list)
    required_themes: list[RequirementItem[str]] = field(default_factory=list)
    banned_topics: list[RequirementItem[str]] = field(default_factory=list)
    banned_words: list[RequirementItem[str]] = field(default_factory=list)

    # Clip Requirements
    duration_min_s: RequirementItem[float] = field(
        default_factory=lambda: RequirementItem(value=20.0, confidence="inferred")
    )
    duration_max_s: RequirementItem[float] = field(
        default_factory=lambda: RequirementItem(value=90.0, confidence="inferred")
    )
    duration_preferred_s: RequirementItem[float | None] = field(
        default_factory=lambda: RequirementItem(value=45.0, confidence="inferred")
    )
    output_count: RequirementItem[int] = field(
        default_factory=lambda: RequirementItem(value=5, confidence="inferred")
    )
    aspect_ratio: RequirementItem[str] = field(
        default_factory=lambda: RequirementItem(value="9:16", confidence="inferred")
    )

    # Hook Rules
    hook_required: RequirementItem[bool] = field(
        default_factory=lambda: RequirementItem(value=True, confidence="inferred")
    )
    hook_window_s: RequirementItem[float] = field(
        default_factory=lambda: RequirementItem(value=2.5, confidence="inferred")
    )
    hook_min_score: RequirementItem[float] = field(
        default_factory=lambda: RequirementItem(value=6.0, confidence="inferred")
    )
    hook_types: list[RequirementItem[str]] = field(default_factory=list)
    hook_instructions: list[RequirementItem[str]] = field(default_factory=list)

    # CTA Rules
    cta_required: RequirementItem[bool] = field(
        default_factory=lambda: RequirementItem(value=False, confidence="inferred")
    )
    cta_types: list[RequirementItem[str]] = field(default_factory=list)
    cta_window_s: RequirementItem[float] = field(
        default_factory=lambda: RequirementItem(value=5.0, confidence="inferred")
    )
    cta_instructions: list[RequirementItem[str]] = field(default_factory=list)

    # Style & Delivery
    tone: RequirementItem[str] = field(
        default_factory=lambda: RequirementItem(value="engaging", confidence="inferred")
    )
    pacing: RequirementItem[str] = field(
        default_factory=lambda: RequirementItem(value="dynamic", confidence="inferred")
    )
    caption_preset: RequirementItem[str] = field(
        default_factory=lambda: RequirementItem(value="bold_pop", confidence="inferred")
    )
    caption_instructions: list[RequirementItem[str]] = field(default_factory=list)
    visual_instructions: list[RequirementItem[str]] = field(default_factory=list)
    branding_rules: list[RequirementItem[str]] = field(default_factory=list)

    # SEO & Metadata Requirements
    title_patterns: list[RequirementItem[str]] = field(default_factory=list)
    description_guidelines: list[RequirementItem[str]] = field(default_factory=list)
    hashtags: list[RequirementItem[str]] = field(default_factory=list)
    keywords: list[RequirementItem[str]] = field(default_factory=list)
    required_mentions: list[RequirementItem[str]] = field(default_factory=list)

    # Platform Requirements
    platforms: list[str] = field(default_factory=lambda: ["telegram", "drive"])
    platform_rules: dict[str, list[RequirementItem[str]]] = field(default_factory=dict)

    # Quality Requirements
    max_silence_s: RequirementItem[float] = field(
        default_factory=lambda: RequirementItem(value=1.5, confidence="inferred")
    )
    min_speech_density: RequirementItem[float] = field(
        default_factory=lambda: RequirementItem(value=1.2, confidence="inferred")
    )

    # Conflicts & Warnings
    conflicts: list[CampaignConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_critical_conflicts(self) -> bool:
        return any(
            c.severity == "critical" and c.resolution_status == "unresolved"
            for c in self.conflicts
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert specification to JSON-serializable dictionary."""
        return {
            "campaign_id": self.campaign_id,
            "title": self.title,
            "description": self.description,
            "objective": self.objective,
            "target_audience": self.target_audience,
            "created_at": self.created_at,
            "campaign_url": self.campaign_url,
            "documents": [doc.to_dict() for doc in self.documents],
            "desired_topics": [item.to_dict() for item in self.desired_topics],
            "preferred_speakers": [item.to_dict() for item in self.preferred_speakers],
            "required_themes": [item.to_dict() for item in self.required_themes],
            "banned_topics": [item.to_dict() for item in self.banned_topics],
            "banned_words": [item.to_dict() for item in self.banned_words],
            "duration_min_s": self.duration_min_s.to_dict(),
            "duration_max_s": self.duration_max_s.to_dict(),
            "duration_preferred_s": self.duration_preferred_s.to_dict(),
            "output_count": self.output_count.to_dict(),
            "aspect_ratio": self.aspect_ratio.to_dict(),
            "hook_required": self.hook_required.to_dict(),
            "hook_window_s": self.hook_window_s.to_dict(),
            "hook_min_score": self.hook_min_score.to_dict(),
            "hook_types": [item.to_dict() for item in self.hook_types],
            "hook_instructions": [item.to_dict() for item in self.hook_instructions],
            "cta_required": self.cta_required.to_dict(),
            "cta_types": [item.to_dict() for item in self.cta_types],
            "cta_window_s": self.cta_window_s.to_dict(),
            "cta_instructions": [item.to_dict() for item in self.cta_instructions],
            "tone": self.tone.to_dict(),
            "pacing": self.pacing.to_dict(),
            "caption_preset": self.caption_preset.to_dict(),
            "caption_instructions": [item.to_dict() for item in self.caption_instructions],
            "visual_instructions": [item.to_dict() for item in self.visual_instructions],
            "branding_rules": [item.to_dict() for item in self.branding_rules],
            "title_patterns": [item.to_dict() for item in self.title_patterns],
            "description_guidelines": [item.to_dict() for item in self.description_guidelines],
            "hashtags": [item.to_dict() for item in self.hashtags],
            "keywords": [item.to_dict() for item in self.keywords],
            "required_mentions": [item.to_dict() for item in self.required_mentions],
            "platforms": self.platforms,
            "platform_rules": {
                k: [i.to_dict() for i in v] for k, v in self.platform_rules.items()
            },
            "max_silence_s": self.max_silence_s.to_dict(),
            "min_speech_density": self.min_speech_density.to_dict(),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "warnings": self.warnings,
            "has_critical_conflicts": self.has_critical_conflicts,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CampaignSpecification:
        """Reconstruct CampaignSpecification from dictionary."""
        spec = cls(
            campaign_id=d.get("campaign_id") or generate_campaign_id(),
            title=d.get("title", "Normalized Campaign"),
            description=d.get("description", ""),
            objective=d.get("objective", ""),
            target_audience=d.get("target_audience", ""),
            created_at=d.get("created_at") or utcnow_str(),
            campaign_url=d.get("campaign_url"),
            documents=[IngestedDocument.from_dict(x) for x in d.get("documents", [])],
            desired_topics=[RequirementItem.from_dict(x) for x in d.get("desired_topics", [])],
            preferred_speakers=[RequirementItem.from_dict(x) for x in d.get("preferred_speakers", [])],
            required_themes=[RequirementItem.from_dict(x) for x in d.get("required_themes", [])],
            banned_topics=[RequirementItem.from_dict(x) for x in d.get("banned_topics", [])],
            banned_words=[RequirementItem.from_dict(x) for x in d.get("banned_words", [])],
            platforms=list(d.get("platforms", ["telegram", "drive"])),
            warnings=list(d.get("warnings", [])),
            conflicts=[CampaignConflict.from_dict(c) for c in d.get("conflicts", [])],
        )

        if "duration_min_s" in d:
            spec.duration_min_s = RequirementItem.from_dict(d["duration_min_s"])
        if "duration_max_s" in d:
            spec.duration_max_s = RequirementItem.from_dict(d["duration_max_s"])
        if "duration_preferred_s" in d:
            spec.duration_preferred_s = RequirementItem.from_dict(d["duration_preferred_s"])
        if "output_count" in d:
            spec.output_count = RequirementItem.from_dict(d["output_count"])
        if "aspect_ratio" in d:
            spec.aspect_ratio = RequirementItem.from_dict(d["aspect_ratio"])
        if "hook_required" in d:
            spec.hook_required = RequirementItem.from_dict(d["hook_required"])
        if "hook_window_s" in d:
            spec.hook_window_s = RequirementItem.from_dict(d["hook_window_s"])
        if "hook_min_score" in d:
            spec.hook_min_score = RequirementItem.from_dict(d["hook_min_score"])
        if "hook_types" in d:
            spec.hook_types = [RequirementItem.from_dict(x) for x in d["hook_types"]]
        if "hook_instructions" in d:
            spec.hook_instructions = [RequirementItem.from_dict(x) for x in d["hook_instructions"]]
        if "cta_required" in d:
            spec.cta_required = RequirementItem.from_dict(d["cta_required"])
        if "cta_types" in d:
            spec.cta_types = [RequirementItem.from_dict(x) for x in d["cta_types"]]
        if "cta_window_s" in d:
            spec.cta_window_s = RequirementItem.from_dict(d["cta_window_s"])
        if "cta_instructions" in d:
            spec.cta_instructions = [RequirementItem.from_dict(x) for x in d["cta_instructions"]]
        if "tone" in d:
            spec.tone = RequirementItem.from_dict(d["tone"])
        if "pacing" in d:
            spec.pacing = RequirementItem.from_dict(d["pacing"])
        if "caption_preset" in d:
            spec.caption_preset = RequirementItem.from_dict(d["caption_preset"])
        if "caption_instructions" in d:
            spec.caption_instructions = [RequirementItem.from_dict(x) for x in d["caption_instructions"]]
        if "visual_instructions" in d:
            spec.visual_instructions = [RequirementItem.from_dict(x) for x in d["visual_instructions"]]
        if "branding_rules" in d:
            spec.branding_rules = [RequirementItem.from_dict(x) for x in d["branding_rules"]]
        if "title_patterns" in d:
            spec.title_patterns = [RequirementItem.from_dict(x) for x in d["title_patterns"]]
        if "description_guidelines" in d:
            spec.description_guidelines = [RequirementItem.from_dict(x) for x in d["description_guidelines"]]
        if "hashtags" in d:
            spec.hashtags = [RequirementItem.from_dict(x) for x in d["hashtags"]]
        if "keywords" in d:
            spec.keywords = [RequirementItem.from_dict(x) for x in d["keywords"]]
        if "required_mentions" in d:
            spec.required_mentions = [RequirementItem.from_dict(x) for x in d["required_mentions"]]
        if "max_silence_s" in d:
            spec.max_silence_s = RequirementItem.from_dict(d["max_silence_s"])
        if "min_speech_density" in d:
            spec.min_speech_density = RequirementItem.from_dict(d["min_speech_density"])

        return spec

    def to_campaign_brief(self) -> CampaignBrief:
        """Convert normalized specification to CampaignBrief for downstream clipping."""
        req_topics = [item.value for item in self.desired_topics]
        req_concepts = [item.value for item in self.required_themes]
        opt_keywords = [item.value for item in self.keywords]
        banned = [item.value for item in self.banned_words] + [item.value for item in self.banned_topics]
        cta_types_list = [item.value for item in self.cta_types]
        hook_types_list = [item.value for item in self.hook_types]

        ratio_val = self.aspect_ratio.value if self.aspect_ratio.value in ("9:16", "1:1", "16:9") else "9:16"

        return CampaignBrief(
            campaign_id=self.campaign_id,
            name=self.title[:80],
            description=self.description,
            topic_context=self.objective or self.description,
            target_audience=self.target_audience,
            required_topics=req_topics[:10],
            required_concepts=req_concepts[:10],
            optional_keywords=opt_keywords[:20],
            banned_words=list(set(banned))[:20],
            minimum_duration=float(self.duration_min_s.value),
            maximum_duration=float(self.duration_max_s.value),
            preferred_duration=float(self.duration_preferred_s.value) if self.duration_preferred_s.value else None,
            hook_required=bool(self.hook_required.value),
            hook_window_seconds=float(self.hook_window_s.value),
            minimum_hook_score=float(self.hook_min_score.value),
            hook_types=hook_types_list,
            cta_required=bool(self.cta_required.value),
            cta_types=cta_types_list,
            cta_window_seconds=float(self.cta_window_s.value),
            tone=str(self.tone.value),
            pacing=str(self.pacing.value),
            aspect_ratio=ratio_val,  # type: ignore
            caption_preset=str(self.caption_preset.value),
            output_count=int(self.output_count.value),
            maximum_silence_seconds=float(self.max_silence_s.value),
            minimum_content_density=float(self.min_speech_density.value),
            hashtags=[item.value for item in self.hashtags],
            title_patterns=[item.value for item in self.title_patterns],
            description_guidelines=[item.value for item in self.description_guidelines],
            required_mentions=[item.value for item in self.required_mentions],
            cta_instructions=[item.value for item in self.cta_instructions],
            branding_rules=[item.value for item in self.branding_rules],
        )

    @classmethod
    def from_campaign_brief(
        cls,
        brief: CampaignBrief | dict[str, Any],
        filename: str = "",
    ) -> CampaignSpecification:
        """Construct a normalized CampaignSpecification from a CampaignBrief or parsed dictionary."""
        if isinstance(brief, dict):
            b_data = brief
        elif hasattr(brief, "model_dump"):
            b_data = brief.model_dump(mode="json")
        else:
            b_data = dict(brief)

        spec = cls(
            campaign_id=b_data.get("campaign_id") or generate_campaign_id(),
            title=b_data.get("name", "Normalized Campaign"),
            description=b_data.get("description", ""),
            objective=b_data.get("topic_context", ""),
            target_audience=b_data.get("target_audience", ""),
        )

        for t in b_data.get("required_topics", []):
            spec.desired_topics.append(RequirementItem(value=t, confidence="explicit", source_filename=filename))
        for c in b_data.get("required_concepts", []):
            spec.required_themes.append(RequirementItem(value=c, confidence="explicit", source_filename=filename))
        for k in b_data.get("optional_keywords", []):
            spec.keywords.append(RequirementItem(value=k, confidence="inferred", source_filename=filename))
        for b in b_data.get("banned_words", []):
            spec.banned_words.append(RequirementItem(value=b, confidence="explicit", source_filename=filename))
        for b in b_data.get("banned_topics", []):
            spec.banned_topics.append(RequirementItem(value=b, confidence="explicit", source_filename=filename))

        if "minimum_duration" in b_data:
            spec.duration_min_s = RequirementItem(value=float(b_data["minimum_duration"]), confidence="explicit", source_filename=filename)
        if "maximum_duration" in b_data:
            spec.duration_max_s = RequirementItem(value=float(b_data["maximum_duration"]), confidence="explicit", source_filename=filename)
        if b_data.get("preferred_duration"):
            spec.duration_preferred_s = RequirementItem(value=float(b_data["preferred_duration"]), confidence="inferred", source_filename=filename)

        if "hook_required" in b_data:
            spec.hook_required = RequirementItem(value=bool(b_data["hook_required"]), confidence="explicit", source_filename=filename)
        if "hook_window_seconds" in b_data:
            spec.hook_window_s = RequirementItem(value=float(b_data["hook_window_seconds"]), confidence="inferred", source_filename=filename)
        if "minimum_hook_score" in b_data:
            spec.hook_min_score = RequirementItem(value=float(b_data["minimum_hook_score"]), confidence="inferred", source_filename=filename)
        for ht in b_data.get("hook_types", []):
            spec.hook_types.append(RequirementItem(value=ht, confidence="inferred", source_filename=filename))

        if "cta_required" in b_data:
            spec.cta_required = RequirementItem(value=bool(b_data["cta_required"]), confidence="explicit", source_filename=filename)
        for ct in b_data.get("cta_types", []):
            spec.cta_types.append(RequirementItem(value=ct, confidence="inferred", source_filename=filename))
        if "cta_window_seconds" in b_data:
            spec.cta_window_s = RequirementItem(value=float(b_data["cta_window_seconds"]), confidence="inferred", source_filename=filename)
        for ci in b_data.get("cta_instructions", []):
            spec.cta_instructions.append(RequirementItem(value=ci, confidence="explicit", source_filename=filename))
        if b_data.get("cta_text"):
            spec.cta_instructions.append(RequirementItem(value=b_data["cta_text"], confidence="explicit", source_filename=filename))

        for h in b_data.get("hashtags", []):
            spec.hashtags.append(RequirementItem(value=h, confidence="explicit", source_filename=filename))
        for p in b_data.get("title_patterns", []):
            spec.title_patterns.append(RequirementItem(value=p, confidence="explicit", source_filename=filename))
        for g in b_data.get("description_guidelines", []):
            spec.description_guidelines.append(RequirementItem(value=g, confidence="explicit", source_filename=filename))
        for m in b_data.get("required_mentions", []):
            spec.required_mentions.append(RequirementItem(value=m, confidence="explicit", source_filename=filename))
        for r in b_data.get("branding_rules", []):
            spec.branding_rules.append(RequirementItem(value=r, confidence="explicit", source_filename=filename))

        if "tone" in b_data:
            spec.tone = RequirementItem(value=str(b_data["tone"]), confidence="inferred", source_filename=filename)
        if "pacing" in b_data:
            spec.pacing = RequirementItem(value=str(b_data["pacing"]), confidence="inferred", source_filename=filename)
        if "aspect_ratio" in b_data:
            spec.aspect_ratio = RequirementItem(value=str(b_data["aspect_ratio"]), confidence="inferred", source_filename=filename)
        if "caption_preset" in b_data:
            spec.caption_preset = RequirementItem(value=str(b_data["caption_preset"]), confidence="inferred", source_filename=filename)
        if "output_count" in b_data:
            spec.output_count = RequirementItem(value=int(b_data["output_count"]), confidence="inferred", source_filename=filename)

        return spec
