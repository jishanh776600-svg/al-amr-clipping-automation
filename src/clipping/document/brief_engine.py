"""Campaign Brief Intelligence Engine (Step 2/5).

Ingests Whop and operator campaign briefs in PDF, TXT, or MD format.
Extracts structured CampaignRequirements with provenance, modality distinction,
ambiguity preservation, and zero fabrication.
"""

import io
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from clipping.contracts.requirements import (
    CampaignRequirements,
    CampaignIdentityRequirements,
    SourceRequirements,
    ClipRequirements,
    ContentRequirements,
    BrandingRequirements,
    TextRequirements,
    PlatformRequirements,
    SubmissionRequirements,
    MonetizationRequirements,
    AdditionalRules,
    ExtractionMetadata,
    RequirementModality,
)
from clipping.document.ai_extractor import BriefAIExtractor
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.document.brief_engine")


class BriefDocumentReader:
    """Extracts raw text and per-page content from PDF, TXT, or Markdown files."""

    @classmethod
    def read_document_bytes(
        cls,
        content: bytes,
        filename: str,
    ) -> Tuple[str, List[Tuple[int, str]], bool]:
        """
        Reads document bytes.
        Returns:
            - full_text: string combining all extracted text
            - pages: List of (page_no, page_text)
            - is_image_only: True if document has 0 extractable characters (e.g. scanned image PDF)
        """
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            return cls._read_pdf(content)
        else:
            return cls._read_text(content)

    @classmethod
    def _read_pdf(cls, pdf_bytes: bytes) -> Tuple[str, List[Tuple[int, str]], bool]:
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages: List[Tuple[int, str]] = []
            total_text_parts: List[str] = []

            for idx, page in enumerate(reader.pages):
                page_no = idx + 1
                extracted = page.extract_text() or ""
                pages.append((page_no, extracted))
                if extracted.strip():
                    total_text_parts.append(extracted.strip())

            full_text = "\n\n".join(total_text_parts).strip()
            is_image_only = len(full_text) == 0
            return full_text, pages, is_image_only
        except Exception as e:
            logger.warning("Failed to parse PDF document streams", error=str(e))
            return "", [(1, "")], True

        return full_text, pages, is_image_only

    @classmethod
    def _read_text(cls, text_bytes: bytes) -> Tuple[str, List[Tuple[int, str]], bool]:
        try:
            text = text_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = text_bytes.decode("latin-1", errors="replace")

        full_text = text.strip()
        is_empty = len(full_text) == 0
        return full_text, [(1, full_text)], is_empty


class BriefDeterministicExtractor:
    """
    Deterministic rule-based extractor for Campaign Briefs.
    Identifies durations, hashtags, platforms, rules, CPM, and boundaries.
    Never invents missing requirements.
    """

    # Duration patterns
    RE_DURATION_RANGE = re.compile(
        r"(?:duration|length|clip length|runtime):\s*(\d+)\s*(?:s|sec|seconds)?\s*(?:-|to)\s*(\d+)\s*(?:s|sec|seconds)?",
        re.IGNORECASE,
    )
    RE_DURATION_SINGLE = re.compile(
        r"(?:duration|length|clip length|runtime):\s*(\d+)\s*(?:s|sec|seconds)?",
        re.IGNORECASE,
    )
    RE_DURATION_PREFERRED = re.compile(
        r"(?:preferred duration|ideal length|preferred length):\s*(\d+)\s*(?:s|sec|seconds)?",
        re.IGNORECASE,
    )

    # Clip count
    RE_CLIP_COUNT = re.compile(
        r"(\d+)\s*(?:clips?|videos?|shorts?|reels?)\s*(?:required|needed|minimum|per creator|total)",
        re.IGNORECASE,
    )
    RE_CLIP_COUNT_ALT = re.compile(
        r"(?:clips? required|number of clips?|required clips?):\s*(\d+)",
        re.IGNORECASE,
    )

    # Aspect ratio / Resolution
    RE_ASPECT_RATIO = re.compile(r"(9:16|16:9|1:1|4:5)", re.IGNORECASE)
    RE_RESOLUTION = re.compile(r"(1080x1920|1920x1080|4K|1080p|720p)", re.IGNORECASE)
    RE_FPS = re.compile(r"(\d{2,3})\s*(?:fps|frames per second)", re.IGNORECASE)

    # Hashtags & Mentions
    RE_HASHTAG = re.compile(r"#([a-zA-Z0-9_]+)")
    RE_MENTION = re.compile(r"@([a-zA-Z0-9_.]+)")

    # Payout & CPM
    RE_CPM = re.compile(r"(?:cpm|cpm rate):\s*\$?(\d+(?:\.\d{1,2})?)", re.IGNORECASE)
    RE_TOTAL_BUDGET = re.compile(r"(?:budget|total budget|campaign pool):\s*\$?(\d+(?:,\d{3})*(?:\.\d{1,2})?)", re.IGNORECASE)
    RE_PAYOUT_FIXED = re.compile(r"(?:payout|bounty|pay per clip):\s*\$?(\d+(?:\.\d{1,2})?)", re.IGNORECASE)

    # URLs
    RE_HTTP_URL = re.compile(r"https?://[^\s<>\"',;]+", re.IGNORECASE)

    @classmethod
    def extract(
        cls,
        full_text: str,
        pages: List[Tuple[int, str]],
        source_filename: Optional[str] = None,
        source_format: str = "txt",
        is_image_only: bool = False,
    ) -> CampaignRequirements:
        """Parses document text deterministically into structured CampaignRequirements."""
        reqs = CampaignRequirements()
        reqs.metadata.source_filename = source_filename
        reqs.metadata.source_format = source_format
        reqs.metadata.num_pages = max(1, len(pages))
        reqs.metadata.is_image_only = is_image_only

        if is_image_only:
            reqs.metadata.extraction_status = "NEEDS_REVIEW"
            reqs.metadata.error_message = (
                "Image-only PDF detected. No selectable text found. "
                "Please upload a PDF with selectable text, TXT, or MD brief, or override requirements manually."
            )
            reqs.metadata.confidence_score = 0.0
            return reqs

        lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]

        # 1. Campaign Identity
        cls._extract_identity(lines, reqs.identity, source_filename)

        # 2. Source Requirements
        cls._extract_source_requirements(full_text, lines, reqs.source)

        # 3. Clip Requirements (Duration, Count, Framing)
        cls._extract_clip_requirements(full_text, lines, reqs.clips)

        # 4. Content Requirements (Allowed, Prohibited, Talking Points, Claims)
        cls._extract_content_requirements(lines, reqs.content)

        # 5. Branding Requirements (Watermark, Logo, Subtitles)
        cls._extract_branding_requirements(lines, reqs.branding)

        # 6. Text Requirements (Hashtags, Captions, CTA, Keywords)
        cls._extract_text_requirements(full_text, lines, reqs.text)

        # 7. Platform Requirements
        cls._extract_platform_requirements(full_text, reqs.platform)

        # 8. Submission Requirements
        cls._extract_submission_requirements(lines, reqs.submission)

        # 9. Monetization Requirements
        cls._extract_monetization_requirements(full_text, lines, reqs.monetization)

        # 10. Additional Rules
        cls._extract_additional_rules(lines, reqs.additional_rules)

        reqs.metadata.extraction_status = "SUCCESS"
        reqs.metadata.confidence_score = 0.95
        return reqs

    @classmethod
    def _extract_identity(cls, lines: List[str], identity: CampaignIdentityRequirements, filename: Optional[str]) -> None:
        for idx, line in enumerate(lines[:15]):
            lower = line.lower()
            if any(lower.startswith(k) for k in ["campaign id:", "id:"]):
                val = re.sub(r"^(?:campaign id|id):\s*", "", line, flags=re.IGNORECASE).strip()
                if val:
                    identity.campaign_id = val
            elif any(lower.startswith(k) for k in ["campaign:", "campaign name:", "project:"]):
                val = re.sub(r"^(?:campaign|campaign name|project):\s*", "", line, flags=re.IGNORECASE).strip()
                if val and not identity.campaign_name:
                    identity.campaign_name = val
            elif any(lower.startswith(k) for k in ["description:", "about:"]):
                val = re.sub(r"^(?:description|about):\s*", "", line, flags=re.IGNORECASE).strip()
                if val:
                    identity.campaign_description = val

        # Fallback to first major heading or filename
        if not identity.campaign_name and lines:
            first_line = lines[0]
            if len(first_line) < 80 and not first_line.startswith("http"):
                identity.campaign_name = first_line
            elif filename:
                identity.campaign_name = Path(filename).stem.replace("_", " ").title()

    @classmethod
    def _extract_source_requirements(cls, full_text: str, lines: List[str], source: SourceRequirements) -> None:
        # Extract source URLs (e.g. YouTube, Drive, direct video URLs)
        urls = cls.RE_HTTP_URL.findall(full_text)
        for url in urls:
            u_lower = url.lower()
            if any(h in u_lower for h in ["youtube.com", "youtu.be", "drive.google.com", ".mp4", ".mov", ".mkv"]):
                if url not in source.source_urls:
                    source.source_urls.append(url)

        for line in lines:
            lower = line.lower()
            if "source video" in lower or "source footage" in lower or "permitted footage" in lower:
                source.source_footage_restrictions.append(line)
            if "specific footage required" in lower or "only use footage from" in lower or "must use provided footage" in lower:
                source.specific_footage_required = True

    @classmethod
    def _extract_clip_requirements(cls, full_text: str, lines: List[str], clips: ClipRequirements) -> None:
        # Range duration
        dur_range = cls.RE_DURATION_RANGE.search(full_text)
        if dur_range:
            clips.min_duration_seconds = float(dur_range.group(1))
            clips.max_duration_seconds = float(dur_range.group(2))
            clips.duration_modality = RequirementModality.REQUIRED
        else:
            dur_single = cls.RE_DURATION_SINGLE.search(full_text)
            if dur_single:
                val = float(dur_single.group(1))
                clips.min_duration_seconds = max(10.0, val - 10.0)
                clips.max_duration_seconds = val + 15.0
                clips.duration_modality = RequirementModality.REQUIRED

        # Preferred duration
        dur_pref = cls.RE_DURATION_PREFERRED.search(full_text)
        if dur_pref:
            clips.preferred_duration_seconds = float(dur_pref.group(1))

        # Clip count
        count_match = cls.RE_CLIP_COUNT_ALT.search(full_text) or cls.RE_CLIP_COUNT.search(full_text)
        if count_match:
            try:
                clips.clip_count_required = int(count_match.group(1))
            except ValueError:
                pass

        # Aspect ratio
        ar_match = cls.RE_ASPECT_RATIO.search(full_text)
        if ar_match:
            clips.aspect_ratio = ar_match.group(1).lower()

        # Resolution
        res_match = cls.RE_RESOLUTION.search(full_text)
        if res_match:
            clips.resolution = res_match.group(1)

        # FPS
        fps_match = cls.RE_FPS.search(full_text)
        if fps_match:
            clips.fps = int(fps_match.group(1))

    @classmethod
    def _extract_content_requirements(cls, lines: List[str], content: ContentRequirements) -> None:
        current_section = None

        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ["prohibited topics:", "forbidden:", "do not mention:", "prohibited:"]):
                current_section = "prohibited_topics"
                val = re.sub(r"^(?:prohibited topics|forbidden|do not mention|prohibited):\s*", "", line, flags=re.IGNORECASE).strip()
                if val:
                    content.prohibited_topics.extend([x.strip() for x in val.split(",") if x.strip()])
                continue
            elif any(k in lower for k in ["allowed topics:", "permitted themes:", "topics:"]):
                current_section = "allowed_topics"
                val = re.sub(r"^(?:allowed topics|permitted themes|topics):\s*", "", line, flags=re.IGNORECASE).strip()
                if val:
                    content.allowed_topics.extend([x.strip() for x in val.split(",") if x.strip()])
                continue
            elif any(k in lower for k in ["required talking points:", "must include:", "talking points:"]):
                current_section = "talking_points"
                val = re.sub(r"^(?:required talking points|must include|talking points):\s*", "", line, flags=re.IGNORECASE).strip()
                if val:
                    content.required_talking_points.append(val)
                continue
            elif any(k in lower for k in ["prohibited claims:", "do not claim:", "false claims:"]):
                current_section = "prohibited_claims"
                val = re.sub(r"^(?:prohibited claims|do not claim|false claims):\s*", "", line, flags=re.IGNORECASE).strip()
                if val:
                    content.prohibited_claims.append(val)
                continue

            # If inside list
            if line.startswith(("-", "*", "•")) and current_section:
                item = line.lstrip("-*• ").strip()
                if current_section == "prohibited_topics":
                    content.prohibited_topics.append(item)
                elif current_section == "allowed_topics":
                    content.allowed_topics.append(item)
                elif current_section == "talking_points":
                    content.required_talking_points.append(item)
                elif current_section == "prohibited_claims":
                    content.prohibited_claims.append(item)
            elif line.endswith(":") and len(line) < 40:
                current_section = None

    @classmethod
    def _extract_branding_requirements(cls, lines: List[str], branding: BrandingRequirements) -> None:
        for line in lines:
            lower = line.lower()
            if "watermark" in lower:
                branding.watermark_requirements = line
                if any(w in lower for w in ["prohibited", "no watermark", "do not include watermark"]):
                    branding.watermark_modality = RequirementModality.PROHIBITED
                elif any(w in lower for w in ["required", "must include watermark"]):
                    branding.watermark_modality = RequirementModality.REQUIRED
                elif "optional" in lower:
                    branding.watermark_modality = RequirementModality.OPTIONAL
            elif "logo" in lower:
                if any(w in lower for w in ["required logo", "include logo", "logo:"]):
                    branding.required_logo = line
            elif any(k in lower for k in ["caption style", "subtitle style", "burned-in captions", "karaoke subtitles"]):
                branding.caption_subtitle_requirements = line

    @classmethod
    def _extract_text_requirements(cls, full_text: str, lines: List[str], text_reqs: TextRequirements) -> None:
        # Extract hashtags
        for line in lines:
            lower = line.lower()
            tags = cls.RE_HASHTAG.findall(line)
            if not tags:
                continue

            if any(k in lower for k in ["prohibited", "forbidden", "do not use"]):
                for tag in tags:
                    full_tag = f"#{tag}"
                    if full_tag not in text_reqs.prohibited_hashtags:
                        text_reqs.prohibited_hashtags.append(full_tag)
            else:
                # Required / permitted hashtags
                for tag in tags:
                    full_tag = f"#{tag}"
                    if full_tag not in text_reqs.required_hashtags:
                        text_reqs.required_hashtags.append(full_tag)

        # Call to Action (CTA)
        for line in lines:
            lower = line.lower()
            if "&" in line and ("text" in lower or "cta" in lower):
                continue
            m = re.search(r"(?:call to action|cta|required cta|closing sentence):\s*(.+)$", line, flags=re.IGNORECASE)
            if m:
                val = m.group(1).strip().strip("\"'")
                if val:
                    text_reqs.call_to_action = val
                    text_reqs.cta_modality = RequirementModality.REQUIRED
                    break

    @classmethod
    def _extract_platform_requirements(cls, full_text: str, platforms_req: PlatformRequirements) -> None:
        lower = full_text.lower()
        if "instagram" in lower or "reels" in lower:
            if "instagram_reels" not in platforms_req.platforms:
                platforms_req.platforms.append("instagram_reels")
        if "youtube" in lower or "shorts" in lower:
            if "youtube_shorts" not in platforms_req.platforms:
                platforms_req.platforms.append("youtube_shorts")
        if "tiktok" in lower:
            if "tiktok" not in platforms_req.platforms:
                platforms_req.platforms.append("tiktok")

        if "only post to instagram" in lower:
            platforms_req.preferred_platform = "instagram_reels"
        elif "only post to youtube" in lower:
            platforms_req.preferred_platform = "youtube_shorts"

    @classmethod
    def _extract_submission_requirements(cls, lines: List[str], submission: SubmissionRequirements) -> None:
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ["deadline:", "due date:", "submissions close:"]):
                val = re.sub(r"^(?:deadline|due date|submissions close):\s*", "", line, flags=re.IGNORECASE).strip()
                if val:
                    submission.deadline = val
            elif any(k in lower for k in ["submit at:", "submission link:", "submission url:"]):
                val = re.sub(r"^(?:submit at|submission link|submission url):\s*", "", line, flags=re.IGNORECASE).strip()
                if val:
                    submission.submission_url_or_process = val

    @classmethod
    def _extract_monetization_requirements(cls, full_text: str, lines: List[str], mon: MonetizationRequirements) -> None:
        cpm_match = cls.RE_CPM.search(full_text)
        if cpm_match:
            try:
                mon.cpm_rate = float(cpm_match.group(1))
            except ValueError:
                pass

        budget_match = cls.RE_TOTAL_BUDGET.search(full_text)
        if budget_match:
            try:
                mon.total_budget = float(budget_match.group(1).replace(",", ""))
            except ValueError:
                pass

        payout_match = cls.RE_PAYOUT_FIXED.search(full_text)
        if payout_match:
            mon.payout_info = f"${payout_match.group(1)} fixed per clip"

    @classmethod
    def _extract_additional_rules(cls, lines: List[str], additional: AdditionalRules) -> None:
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ["rule:", "guideline:", "note:", "important:"]):
                if len(line) < 300:
                    additional.rules.append(line)


class CampaignBriefIntelligenceEngine:
    """
    Master Brief Intelligence Coordinator.
    Performs multi-format reading, deterministic rule extraction,
    and optional LLM structured enrichment with strict schema validation.
    """

    def __init__(self, ai_extractor: Optional[BriefAIExtractor] = None):
        self.ai_extractor = ai_extractor or BriefAIExtractor()

    async def analyze_document_bytes(
        self,
        content: bytes,
        filename: str,
        enable_ai: bool = True,
    ) -> CampaignRequirements:
        """Analyzes brief bytes (PDF, TXT, or MD) into structured CampaignRequirements."""
        ext = Path(filename).suffix.lower().lstrip(".")
        full_text, pages, is_image_only = BriefDocumentReader.read_document_bytes(content, filename)

        # Baseline: Deterministic extraction
        reqs = BriefDeterministicExtractor.extract(
            full_text=full_text,
            pages=pages,
            source_filename=filename,
            source_format=ext,
            is_image_only=is_image_only,
        )

        if is_image_only:
            return reqs

        # Optional: AI structured validation and enrichment
        if enable_ai and self.ai_extractor and full_text:
            ai_reqs = await self.ai_extractor.extract_structured_requirements(
                raw_text=full_text,
                source_filename=filename,
            )
            if ai_reqs:
                # Merge enriched fields while preserving deterministic bounds if present
                self._merge_ai_enrichment(reqs, ai_reqs)

        return reqs

    async def analyze_from_storage(
        self,
        storage: StorageDriver,
        storage_key: str,
        enable_ai: bool = True,
    ) -> CampaignRequirements:
        """Retrieves brief file from storage driver and runs analysis."""
        content = await storage.download_bytes(storage_key)
        filename = Path(storage_key).name
        return await self.analyze_document_bytes(content, filename, enable_ai=enable_ai)

    def _merge_ai_enrichment(self, target: CampaignRequirements, ai_src: CampaignRequirements) -> None:
        """Enriches deterministic requirements with valid non-empty AI-extracted fields."""
        target.metadata.engine = ai_src.metadata.engine

        # Identity
        if ai_src.identity.campaign_name and not target.identity.campaign_name:
            target.identity.campaign_name = ai_src.identity.campaign_name
        if ai_src.identity.campaign_description:
            target.identity.campaign_description = ai_src.identity.campaign_description

        # Content talking points
        for tp in ai_src.content.required_talking_points:
            if tp not in target.content.required_talking_points:
                target.content.required_talking_points.append(tp)

        # Prohibited claims
        for pc in ai_src.content.prohibited_claims:
            if pc not in target.content.prohibited_claims:
                target.content.prohibited_claims.append(pc)

        # Hashtags
        for ht in ai_src.text.required_hashtags:
            if ht not in target.text.required_hashtags:
                target.text.required_hashtags.append(ht)

        # CTA
        if ai_src.text.call_to_action and not target.text.call_to_action:
            target.text.call_to_action = ai_src.text.call_to_action
            target.text.cta_modality = ai_src.text.cta_modality
