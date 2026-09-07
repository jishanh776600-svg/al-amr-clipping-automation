"""Strict Production Compliance Gate and Authoritative Metadata Generator."""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel

from clipping.contracts.production import ProductionArtifact, ProductionComplianceResult
from clipping.contracts.requirements import CampaignRequirements
from clipping.contracts.source import SourceResolutionResult, SourceAccessStatus
from clipping.agent.vault.models import AccountMetadata, AccountStatus
from clipping.logging.logger import get_logger

logger = get_logger("clipping.production.compliance_gate")


class GeneratedClipMetadata(BaseModel):
    """Authoritative generated textual metadata."""
    title: str
    description: str
    caption: str
    youtube_description: str
    hashtags: List[str]
    mentions: List[str]
    cta: str
    keywords_used: List[str]
    prohibited_words_found: List[str]


class ProductionComplianceGate:
    """
    Evaluates generated clip artifacts and metadata against CampaignRequirements.
    Blocks review-ready status on any hard non-compliance with zero fabricated passes.
    """

    def generate_metadata(
        self,
        clip_index: int,
        hook: str,
        transcript_snippet: str,
        requirements: Optional[CampaignRequirements] = None,
        campaign_name: str = "AL AMR Campaign",
    ) -> GeneratedClipMetadata:
        """Generates title, captions, descriptions, and hashtags obeying all brief rules."""
        # Clean hook for title
        clean_hook = hook.strip().rstrip(".!?:") if hook else f"Insight #{clip_index}"
        if len(clean_hook) > 80:
            clean_hook = clean_hook[:77] + "..."
        title = f"{clean_hook} | {campaign_name}"

        # Hashtags
        tags: List[str] = []
        if requirements and requirements.text and requirements.text.required_hashtags:
            for ht in requirements.text.required_hashtags:
                ht_clean = ht.strip()
                if ht_clean:
                    if not ht_clean.startswith("#"):
                        ht_clean = f"#{ht_clean}"
                    if ht_clean not in tags:
                        tags.append(ht_clean)
        # Default safety tags if none specified
        if not tags:
            tags = ["#shorts", "#reels", "#viral", "#clipping"]

        # Mentions
        mentions: List[str] = []
        if requirements and requirements.text and requirements.text.mention_handles:
            for mh in requirements.text.mention_handles:
                mh_clean = mh.strip()
                if mh_clean:
                    if not mh_clean.startswith("@"):
                        mh_clean = f"@{mh_clean}"
                    if mh_clean not in mentions:
                        mentions.append(mh_clean)

        # CTA
        cta = ""
        if requirements and requirements.text and requirements.text.call_to_action:
            cta = requirements.text.call_to_action.strip()
        elif requirements and requirements.content and getattr(requirements.content, "cta", None):
            cta = requirements.content.cta.strip()

        # Keywords
        req_keywords: List[str] = []
        if requirements and requirements.text and requirements.text.required_keywords:
            req_keywords = [kw.strip() for kw in requirements.text.required_keywords if kw.strip()]

        # Instagram Caption
        body_text = transcript_snippet[:250].strip() if transcript_snippet else clean_hook
        caption_lines = [
            clean_hook,
            "",
            body_text,
        ]
        if cta:
            caption_lines.extend(["", f"👉 {cta}"])
        if mentions:
            caption_lines.extend(["", " ".join(mentions)])
        caption_lines.extend(["", " ".join(tags)])
        caption = "\n".join(caption_lines)

        # YouTube Description
        yt_desc_lines = [
            f"{clean_hook} - Clip #{clip_index}",
            "",
            body_text,
        ]
        if cta:
            yt_desc_lines.extend(["", cta])
        if mentions:
            yt_desc_lines.extend(["", "Featured: " + ", ".join(mentions)])
        yt_desc_lines.extend(["", " ".join(tags)])
        youtube_desc = "\n".join(yt_desc_lines)

        # Check prohibited words
        prohibited_words: List[str] = []
        if requirements and requirements.text and requirements.text.prohibited_words:
            prohibited_words = [pw.lower() for pw in requirements.text.prohibited_words if pw]

        all_text = f"{title} {caption} {youtube_desc}".lower()
        found_prohibited = [pw for pw in prohibited_words if pw in all_text]

        return GeneratedClipMetadata(
            title=title,
            description=youtube_desc,
            caption=caption,
            youtube_description=youtube_desc,
            hashtags=tags,
            mentions=mentions,
            cta=cta,
            keywords_used=[kw for kw in req_keywords if kw.lower() in all_text],
            prohibited_words_found=found_prohibited,
        )

    def evaluate_clip(
        self,
        artifact: ProductionArtifact,
        source_result: SourceResolutionResult,
        requirements: Optional[CampaignRequirements] = None,
        target_account: Optional[AccountMetadata] = None,
        target_platform: str = "youtube_shorts",
    ) -> ProductionComplianceResult:
        """
        Conducts a strict, comprehensive compliance audit across:
        SOURCE, DURATION, ASPECT RATIO, RESOLUTION, TOPICS, PROHIBITED WORDS,
        WATERMARKS, CAPTIONS, HASHTAGS, CTA, PLATFORM, and DESTINATION ACCOUNT.
        """
        blockers: List[str] = []
        warnings: List[str] = []
        checks: Dict[str, Dict[str, Any]] = {}
        evidence: Dict[str, Any] = {}

        # 1. SOURCE CHECK
        src_status = "PASS"
        if source_result.source_access_status != SourceAccessStatus.ACCESSIBLE:
            src_status = "FAIL"
            err = source_result.failure_reason or "Source video inaccessible"
            blockers.append(f"PRODUCTION BLOCKED | Reason: Source media is not accessible ({err}) | Required action: Supply a valid accessible video stream or file")
        checks["source"] = {"status": src_status, "source_type": source_result.source_type, "uri": source_result.original_uri}
        evidence["source_checksum"] = source_result.checksum

        # 2. DURATION CHECK
        dur_status = "PASS"
        min_dur = 10.0
        max_dur = 60.0
        if requirements and requirements.clips:
            if requirements.clips.min_duration_seconds:
                min_dur = float(requirements.clips.min_duration_seconds)
            if requirements.clips.max_duration_seconds:
                max_dur = float(requirements.clips.max_duration_seconds)

        if artifact.duration < min_dur:
            dur_status = "FAIL"
            blockers.append(
                f"PRODUCTION BLOCKED | Reason: Clip duration ({artifact.duration:.1f}s) is below required minimum ({min_dur:.1f}s) | Required action: Adjust segment trimming to at least {min_dur:.1f}s"
            )
        elif artifact.duration > max_dur:
            dur_status = "FAIL"
            blockers.append(
                f"PRODUCTION BLOCKED | Reason: Clip duration ({artifact.duration:.1f}s) exceeds required maximum ({max_dur:.1f}s) | Required action: Trim segment to at most {max_dur:.1f}s"
            )
        checks["duration"] = {"status": dur_status, "duration": artifact.duration, "min": min_dur, "max": max_dur}
        evidence["duration"] = artifact.duration

        # 3. ASPECT RATIO CHECK
        ar_status = "PASS"
        if artifact.aspect_ratio != "9:16":
            ar_status = "FAIL"
            blockers.append(
                f"PRODUCTION BLOCKED | Reason: Aspect ratio '{artifact.aspect_ratio}' is invalid for vertical media | Required action: Re-render with strict 9:16 vertical crop"
            )
        checks["aspect_ratio"] = {"status": ar_status, "aspect_ratio": artifact.aspect_ratio}

        # 4. RESOLUTION CHECK
        res_status = "PASS"
        if artifact.resolution and ("1080" not in artifact.resolution and "1920" not in artifact.resolution):
            res_status = "FAIL"
            blockers.append(
                f"PRODUCTION BLOCKED | Reason: Output resolution ({artifact.resolution}) does not meet HD standard | Required action: Render at minimum 1080x1920"
            )
        checks["resolution"] = {"status": res_status, "resolution": artifact.resolution}

        # 5. PROHIBITED TOPICS & CONTENT
        proh_status = "PASS"
        if requirements:
            prohibited: List[str] = []
            if requirements.content and requirements.content.prohibited_topics:
                prohibited.extend(requirements.content.prohibited_topics)
            if requirements.source and requirements.source.prohibited_content:
                prohibited.extend(requirements.source.prohibited_content)

            full_clip_text = f"{artifact.title} {artifact.description} {artifact.caption} {artifact.transcript or ''}".lower()
            for prob in prohibited:
                if prob and prob.lower() in full_clip_text:
                    proh_status = "FAIL"
                    blockers.append(
                        f"PRODUCTION BLOCKED | Reason: Prohibited topic/content detected: '{prob}' | Required action: Select an alternate moment without prohibited themes"
                    )
        checks["prohibited_topics"] = {"status": proh_status}

        # 6. PROHIBITED WORDS IN METADATA
        words_status = "PASS"
        if requirements and requirements.text and requirements.text.prohibited_words:
            all_meta_text = f"{artifact.title} {artifact.description} {artifact.caption}".lower()
            for pw in requirements.text.prohibited_words:
                if pw and pw.lower() in all_meta_text:
                    words_status = "FAIL"
                    blockers.append(
                        f"PRODUCTION BLOCKED | Reason: Prohibited word '{pw}' found in generated metadata | Required action: Regenerate titles/captions without '{pw}'"
                    )
        checks["prohibited_words"] = {"status": words_status}

        # 7. REQUIRED HASHTAGS
        tags_status = "PASS"
        if requirements and requirements.text and requirements.text.required_hashtags:
            artifact_tags_lower = [t.lower() for t in artifact.hashtags]
            for req_tag in requirements.text.required_hashtags:
                clean_tag = req_tag.strip().lower()
                if not clean_tag.startswith("#"):
                    clean_tag = f"#{clean_tag}"
                if clean_tag not in artifact_tags_lower:
                    tags_status = "FAIL"
                    blockers.append(
                        f"PRODUCTION BLOCKED | Reason: Mandatory hashtag '{clean_tag}' missing from artifact | Required action: Include all brief-required hashtags"
                    )
        checks["hashtags"] = {"status": tags_status, "count": len(artifact.hashtags)}

        # 8. CALL TO ACTION
        cta_status = "PASS"
        if requirements and requirements.text and requirements.text.call_to_action:
            req_cta = requirements.text.call_to_action.strip().lower()
            if req_cta not in (artifact.caption + " " + artifact.description).lower():
                cta_status = "FAIL"
                blockers.append(
                    f"PRODUCTION BLOCKED | Reason: Mandatory CTA '{req_cta}' missing from caption/description | Required action: Append required call to action"
                )
        checks["call_to_action"] = {"status": cta_status}

        # 9. WATERMARK REQUIREMENT
        wm_status = "PASS"
        if requirements and requirements.branding and (requirements.branding.required_watermark or requirements.branding.watermark_requirements):
            if artifact.branding_status != "applied":
                wm_status = "FAIL"
                blockers.append(
                    "PRODUCTION BLOCKED | Reason: Mandatory watermark was not applied to vertical video | Required action: Re-render with watermark filtergraph enabled"
                )
        checks["watermark"] = {"status": wm_status, "branding_status": artifact.branding_status}

        # 10. SUBTITLE REQUIREMENT
        sub_status = "PASS"
        if requirements and requirements.text and getattr(requirements.text, "subtitles_required", False):
            if not artifact.transcript:
                sub_status = "FAIL"
                blockers.append(
                    "PRODUCTION BLOCKED | Reason: Subtitles required by brief but transcript is missing | Required action: Transcribe audio and apply kinetic captions"
                )
        checks["subtitles"] = {"status": sub_status}

        # 11. PLATFORM FIT
        plat_status = "PASS"
        clean_plat = target_platform.lower()
        if "youtube" not in clean_plat and "instagram" not in clean_plat:
            plat_status = "FAIL"
            blockers.append(
                f"PRODUCTION BLOCKED | Reason: Unsupported target platform '{target_platform}' | Required action: Select YouTube Shorts or Instagram Reels"
            )
        elif requirements and requirements.platform:
            allowed_p = requirements.platform.target_platforms or requirements.platform.platforms or []
            if allowed_p:
                allowed_p_lower = [p.lower() for p in allowed_p]
                if not any(clean_plat in p or p in clean_plat for p in allowed_p_lower):
                    plat_status = "FAIL"
                    blockers.append(
                        f"PRODUCTION BLOCKED | Reason: Platform '{target_platform}' is not permitted by campaign brief: {allowed_p} | Required action: Switch target platform"
                    )
        checks["platform"] = {"status": plat_status, "platform": target_platform}

        # 12. DESTINATION ACCOUNT
        acc_status = "PASS"
        if not target_account:
            acc_status = "FAIL"
            blockers.append(
                "PRODUCTION BLOCKED | Reason: No destination account configured | Required action: Enroll verified active account in vault"
            )
        elif target_account.status != AccountStatus.ACTIVE:
            acc_status = "FAIL"
            blockers.append(
                f"PRODUCTION BLOCKED | Reason: Target account '{target_account.username}' is not active ({target_account.status.value}) | Required action: Re-verify account in vault"
            )
        checks["account"] = {"status": acc_status}

        is_compliant = len(blockers) == 0

        # Compute coverage score
        total_checks = len(checks)
        passed_checks = sum(1 for c in checks.values() if c.get("status") == "PASS")
        coverage = round(passed_checks / max(1, total_checks), 2)

        return ProductionComplianceResult(
            is_compliant=is_compliant,
            blockers=blockers,
            warnings=warnings,
            checks=checks,
            evidence=evidence,
            requirement_coverage=coverage,
            generated_metadata_validation={
                "title_length": len(artifact.title),
                "hashtags_count": len(artifact.hashtags),
                "mentions_count": len(artifact.mentions),
            },
            evaluated_at=datetime.now(timezone.utc),
        )
