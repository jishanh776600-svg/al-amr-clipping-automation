"""Production-grade SEO engine for AL AMR clipping automation."""

from __future__ import annotations

import logging
import re
from typing import Any

from autoclip.campaign.models_intelligence import CampaignSpecification
from autoclip.db.models import Clip, ClipCandidateRecord, ClipMetadataRecord, new_id, utcnow
from autoclip.db import store
from .extractor import extract_campaign_seo_requirements, extract_campaign_seo_spec
from .models import (
    CampaignSEORequirements,
    CampaignSEOSpec,
    ComplianceResult,
    ComplianceStatus,
    DualPlatformMetadata,
    InstagramMetadata,
    YouTubeMetadata,
)
from .quality_gate import MetadataQualityGate, validate_instagram_metadata, validate_youtube_metadata

log = logging.getLogger(__name__)


def _extract_coherent_sentences(text: str, min_chars: int = 40, max_chars: int = 220) -> str:
    """Extract complete sentences ending on punctuation boundaries without mid-word cut-offs."""
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", text).strip()
    clean = re.sub(r"\.{2,}", "", clean)
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    acc = []
    total_len = 0
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if total_len + len(s) + 1 <= max_chars:
            acc.append(s)
            total_len += len(s) + 1
        else:
            break
    if acc:
        res = " ".join(acc).strip()
        if len(res) >= min_chars:
            return res

    words = clean.split()
    chosen: list[str] = []
    c_len = 0
    for w in words:
        if c_len + len(w) + 1 <= max_chars - 1:
            chosen.append(w)
            c_len += len(w) + 1
        else:
            break
    while chosen and chosen[-1].lower() in (
        "and", "or", "so", "the", "a", "an", "to", "of", "in", "for",
        "with", "is", "at", "by", "that", "fo", "we", "he", "she", "it", "but"
    ):
        chosen.pop()
    if not chosen:
        return ""
    res = " ".join(chosen).rstrip(",;:- ") + "."
    return res[0].upper() + res[1:]


class SEOEngine:
    """Generates, validates, and manages per-short SEO and publishing metadata."""

    def __init__(
        self,
        requirements: CampaignSEORequirements | None = None,
        campaign_seo_spec: CampaignSEOSpec | None = None,
    ) -> None:
        self.reqs = requirements or CampaignSEORequirements()
        self.seo_spec = campaign_seo_spec or CampaignSEOSpec()
        self.quality_gate = MetadataQualityGate(self.reqs)

    @classmethod
    def from_campaign_spec(cls, campaign_spec: CampaignSpecification | None) -> SEOEngine:
        reqs = extract_campaign_seo_requirements(campaign_spec)
        seo_spec = extract_campaign_seo_spec(campaign_spec)
        return cls(requirements=reqs, campaign_seo_spec=seo_spec)

    def _resolve_clip_hashtags(
        self,
        platform: str,
        clip: Clip,
        slice_text: str,
        raw_tags: list[str],
    ) -> list[str]:
        """Resolve precise hashtags avoiding cross-show spam tagging."""
        content_text = f"{clip.title or ''} {clip.hook or ''} {slice_text}".lower()
        show_mappings = getattr(self.seo_spec, "show_mappings", []) or []

        matched_show_tag: str | None = None
        other_show_tags: set[str] = set()

        if show_mappings:
            for mapping in show_mappings:
                show_h = mapping.get("hashtag", "").lower()
                show_name = mapping.get("show", "").lower()
                talent = mapping.get("talent", "").lower()

                # Check if this show or talent is mentioned
                is_match = False
                if talent and talent in content_text:
                    is_match = True
                elif show_name and show_name in content_text:
                    is_match = True
                elif show_h and show_h.lstrip("#") in content_text:
                    is_match = True

                if is_match and not matched_show_tag:
                    matched_show_tag = mapping.get("hashtag")
                else:
                    if mapping.get("hashtag"):
                        other_show_tags.add(mapping["hashtag"].lower())

        # If none matched yet, check against show hashtags in content
        if not matched_show_tag and show_mappings:
            for mapping in show_mappings:
                h = mapping.get("hashtag", "")
                if h and h.lower().lstrip("#") in content_text:
                    matched_show_tag = h
                    break
            if not matched_show_tag and show_mappings:
                matched_show_tag = show_mappings[0].get("hashtag")

        base_platform_tag = "#Shorts" if platform == "youtube" else "#reels"
        resolved: list[str] = [base_platform_tag]

        if matched_show_tag:
            norm_m = matched_show_tag if matched_show_tag.startswith("#") else f"#{matched_show_tag}"
            if norm_m.lower() not in [t.lower() for t in resolved]:
                resolved.append(norm_m)

        for t in raw_tags:
            norm_t = t if t.startswith("#") else f"#{t}"
            # Skip if this tag belongs to a DIFFERENT show in show_mappings!
            if norm_t.lower() in other_show_tags and norm_t.lower() != (matched_show_tag or "").lower():
                continue
            if norm_t.lower() not in [x.lower() for x in resolved]:
                resolved.append(norm_t)

        return resolved

    def generate_youtube_metadata(
        self,
        clip: Clip,
        transcript_text: str = "",
        candidate: ClipCandidateRecord | None = None,
    ) -> YouTubeMetadata:
        """Generates independent YouTube Shorts metadata obeying campaign requirements and YouTube constraints."""
        hook = (candidate.hook_text if candidate and candidate.hook_text else clip.hook) or ""
        slice_text = (candidate.transcript_slice if candidate and candidate.transcript_slice else transcript_text) or ""
        topic_cue = clip.title or (f"Highlight #{clip.rank}" if clip.rank else "Key Insight")

        yt_rules = self.seo_spec.youtube_rules if self.seo_spec else None

        # 1. Synthesize YouTube Title (<= 100 chars; sweet spot 30-75 chars)
        title = self._synthesize_title(hook=hook, topic=topic_cue, slice_text=slice_text)
        if len(title) > 100:
            title = title[:97] + "..."

        # 2. Synthesize YouTube Description
        desc_parts: list[str] = []
        summary = _extract_coherent_sentences(slice_text, min_chars=30, max_chars=220)
        if not summary:
            clean_h = re.sub(r"\.{2,}", "", hook).strip().rstrip(".!?,")
            summary = clean_h if clean_h else f"An essential breakdown on {topic_cue}."
            if not summary.endswith("."):
                summary += "."
        desc_parts.append(summary)

        # YouTube campaign phrases
        req_phrases = []
        if self.seo_spec:
            req_phrases = [
                p for p in (self.seo_spec.youtube_rules.required_phrases or self.seo_spec.global_rules.required_terms)
                if len(p) > 3 and not any(w in p.lower() for w in ("rejection", "payout", "submit", "tier-1", "late", "must"))
            ]
        if req_phrases:
            desc_parts.append(f"Key Focus: {', '.join(req_phrases)}")

        # YouTube CTA (channel subscribe + engagement)
        if yt_rules and yt_rules.cta_rules:
            yt_cta = yt_rules.cta_rules[0]
        elif self.reqs.brand_name:
            yt_cta = f"👉 Subscribe to {self.reqs.brand_name} for more exclusive drops and official updates."
        else:
            yt_cta = "👉 Subscribe for more highlights and official updates."
        desc_parts.append(yt_cta)

        # YouTube Links
        yt_links: list[str] = []
        if yt_rules and yt_rules.links:
            yt_links = list(yt_rules.links)
        elif self.seo_spec and self.seo_spec.global_rules.links:
            yt_links = list(self.seo_spec.global_rules.links)
        elif self.reqs.campaign_url:
            yt_links = [self.reqs.campaign_url.strip()]

        for link in yt_links:
            desc_parts.append(f"🔗 Official link: {link}")

        # YouTube Mentions
        yt_mentions: list[str] = []
        if yt_rules and yt_rules.mention_rules:
            yt_mentions = list(yt_rules.mention_rules)
        elif self.reqs.required_mentions:
            tv_m = [m for m in self.reqs.required_mentions if "tv" in m.lower()]
            yt_mentions = tv_m if tv_m else list(self.reqs.required_mentions)

        seen_yt_m = set()
        clean_yt_mentions = []
        for m in yt_mentions:
            norm_m = m if m.startswith("@") else f"@{m}"
            if norm_m.lower() not in seen_yt_m:
                seen_yt_m.add(norm_m.lower())
                clean_yt_mentions.append(norm_m)
        yt_mentions = clean_yt_mentions

        if yt_mentions:
            desc_parts.append(f"Tagging {' '.join(yt_mentions)}")

        # Tags & #Shorts
        custom_yt_tags = []
        if yt_rules and yt_rules.hashtag_rules:
            custom_yt_tags = yt_rules.hashtag_rules
        elif self.reqs.required_hashtags:
            custom_yt_tags = self.reqs.required_hashtags

        yt_tags = self._resolve_clip_hashtags("youtube", clip, slice_text, custom_yt_tags)

        words = re.findall(r"\b[A-Za-z]{4,15}\b", slice_text)
        stopwords = {"this", "that", "with", "from", "have", "they", "will", "what", "when", "there", "about", "your", "more", "into", "their"}
        for w in words:
            if w.lower() not in stopwords and len(yt_tags) < 6:
                tag = f"#{w.capitalize()}"
                if tag.lower() not in [t.lower() for t in yt_tags]:
                    yt_tags.append(tag)

        desc_parts.append(" ".join(yt_tags))
        desc = "\n\n".join(desc_parts)

        yt_meta = YouTubeMetadata(
            title=title,
            description=desc,
            hashtags=yt_tags,
            tags=[t.lstrip("#") for t in yt_tags],
            mentions=yt_mentions,
            links=yt_links,
            cta=yt_cta,
        )

        # 3. Deterministic Validation & Bounded Repair Loop (max 3 rounds)
        for _ in range(3):
            yt_meta = validate_youtube_metadata(yt_meta, spec=self.seo_spec, transcript=slice_text)
            if yt_meta.is_compliant:
                break
            # Repair title length
            if len(yt_meta.title) > 100:
                yt_meta.title = yt_meta.title[:97] + "..."
            if not yt_meta.title:
                yt_meta.title = "Key Insight & Breakdown"
            # Remove prohibited terms
            prohibited = set(self.reqs.prohibited_terms)
            if self.seo_spec:
                prohibited.update(self.seo_spec.global_rules.prohibited_terms)
                prohibited.update(self.seo_spec.youtube_rules.prohibited_terms)
            for pt in prohibited:
                if pt and pt.lower() in yt_meta.title.lower():
                    yt_meta.title = re.sub(rf"\b{re.escape(pt)}\b", "", yt_meta.title, flags=re.IGNORECASE).strip()
                if pt and pt.lower() in yt_meta.description.lower():
                    yt_meta.description = re.sub(rf"\b{re.escape(pt)}\b", "", yt_meta.description, flags=re.IGNORECASE).strip()
            # Ensure required phrases
            for rp in (self.seo_spec.youtube_rules.required_phrases if self.seo_spec else []):
                if rp.lower() not in yt_meta.title.lower() and rp.lower() not in yt_meta.description.lower():
                    yt_meta.description += f"\n\nTopic: {rp}"
            # Ensure required mentions
            for req_m in yt_meta.mentions:
                if req_m.lower() not in yt_meta.description.lower():
                    yt_meta.description += f"\n\nTagging {req_m}"

        return yt_meta

    def generate_instagram_metadata(
        self,
        clip: Clip,
        transcript_text: str = "",
        candidate: ClipCandidateRecord | None = None,
    ) -> InstagramMetadata:
        """Generates independent Instagram Reels metadata obeying campaign requirements and IG constraints."""
        hook = (candidate.hook_text if candidate and candidate.hook_text else clip.hook) or ""
        slice_text = (candidate.transcript_slice if candidate and candidate.transcript_slice else transcript_text) or ""
        topic_cue = clip.title or (f"Highlight #{clip.rank}" if clip.rank else "Key Insight")

        ig_rules = self.seo_spec.instagram_rules if self.seo_spec else None

        # 1. First-Line Hook: Must be <= 125 chars (the fold before '...more' on mobile)
        clean_hook = re.sub(r"\.{2,}", "", hook).strip().rstrip(".!?,;: ")
        if clean_hook.islower():
            clean_hook = clean_hook.title()
        from .sanitizer import sanitize_public_text
        first_line_hook = sanitize_public_text(clean_hook, is_title=True)
        if not first_line_hook or len(first_line_hook) < 10:
            first_line_hook = f"The Real Secret Behind {topic_cue}"
        if len(first_line_hook) > 120:
            first_line_hook = first_line_hook[:120].rsplit(" ", 1)[0]
        first_line_hook = sanitize_public_text(first_line_hook, is_title=True)

        # 2. Instagram Caption Structure with clear line breaks
        caption_lines: list[str] = [first_line_hook, ""]

        ig_body = _extract_coherent_sentences(slice_text, min_chars=30, max_chars=220)
        if not ig_body:
            ig_body = f"Key insight on {topic_cue}: break down what actually works."
        caption_lines.append(ig_body)
        caption_lines.append("")

        # Instagram required mentions
        ig_mentions: list[str] = []
        raw_ig_candidates = []
        if ig_rules and ig_rules.mention_rules:
            raw_ig_candidates = list(ig_rules.mention_rules)
        elif self.reqs.required_mentions:
            raw_ig_candidates = list(self.reqs.required_mentions)

        # Exclude handles with "TV" (which are YouTube handles) unless no other handles exist
        non_tv_candidates = [m for m in raw_ig_candidates if "tv" not in m.lower()]
        chosen_candidates = non_tv_candidates if non_tv_candidates else raw_ig_candidates

        for m in chosen_candidates:
            norm_m = m if m.startswith("@") else f"@{m}"
            if norm_m.lower() not in [x.lower() for x in ig_mentions]:
                ig_mentions.append(norm_m)

        target_handle = ig_mentions[0] if ig_mentions else (f"@{self.reqs.brand_name.lower().replace(' ', '')}" if self.reqs.brand_name else "")
        if ig_rules and ig_rules.cta_rules:
            ig_cta = ig_rules.cta_rules[0]
        elif target_handle:
            ig_cta = f"👉 Follow {target_handle} for daily show highlights and exclusive drops."
        else:
            ig_cta = "👉 Follow for daily highlights and exclusive drops."
        caption_lines.append(ig_cta)

        if ig_mentions:
            caption_lines.append(f"Tagging {' '.join(ig_mentions)}")

        caption_lines.append("")

        # Instagram Hashtags
        custom_ig_tags = []
        if ig_rules and ig_rules.hashtag_rules:
            custom_ig_tags = ig_rules.hashtag_rules
        elif self.reqs.required_hashtags:
            custom_ig_tags = self.reqs.required_hashtags

        ig_tags = self._resolve_clip_hashtags("instagram", clip, slice_text, custom_ig_tags)

        caption_lines.append(" ".join(ig_tags[:6]))
        full_caption = sanitize_public_text("\n".join(caption_lines), is_title=False)

        ig_meta = InstagramMetadata(
            caption=full_caption,
            first_line_hook=first_line_hook,
            hashtags=ig_tags[:6],
            mentions=ig_mentions,
            links=[],  # Raw clickable links not supported in IG caption
            cta=ig_cta,
        )

        # 3. Deterministic Validation & Bounded Repair Loop (max 3 rounds)
        for _ in range(3):
            ig_meta = validate_instagram_metadata(ig_meta, spec=self.seo_spec, transcript=slice_text)
            if ig_meta.is_compliant:
                break
            # Repair caption length
            if len(ig_meta.caption) > 2200:
                ig_meta.caption = ig_meta.caption[:2190] + "..."
            # Remove prohibited terms
            prohibited = set(self.reqs.prohibited_terms)
            if self.seo_spec:
                prohibited.update(self.seo_spec.global_rules.prohibited_terms)
                prohibited.update(self.seo_spec.instagram_rules.prohibited_terms)
            for pt in prohibited:
                if pt and pt.lower() in ig_meta.caption.lower():
                    ig_meta.caption = re.sub(rf"\b{re.escape(pt)}\b", "", ig_meta.caption, flags=re.IGNORECASE).strip()
            # Ensure required mentions are present in caption
            for req_m in ig_meta.mentions:
                if req_m.lower() not in ig_meta.caption.lower():
                    ig_meta.caption += f"\n\nFollow {req_m}"

        return ig_meta

    def generate_for_clip(
        self,
        clip: Clip,
        transcript_text: str = "",
        candidate: ClipCandidateRecord | None = None,
    ) -> ClipMetadataRecord:
        """Generates clip-specific draft metadata and validates compliance."""
        existing = store.get_clip_metadata(clip.id)
        if existing:
            # If operator edited it (version > 1 or final != generated), preserve operator edits!
            if existing.version > 1 or (existing.final_title != existing.generated_title):
                log.info("Preserving existing operator-edited metadata for clip %s (v%d)", clip.id, existing.version)
                return existing

        hook = (candidate.hook_text if candidate and candidate.hook_text else clip.hook) or ""
        slice_text = (candidate.transcript_slice if candidate and candidate.transcript_slice else transcript_text) or ""
        topic_cue = clip.title or (f"Highlight #{clip.rank}" if clip.rank else "Key Insight")

        # Generate YouTube & Instagram Metadata independently
        yt_meta = self.generate_youtube_metadata(clip=clip, transcript_text=slice_text, candidate=candidate)
        ig_meta = self.generate_instagram_metadata(clip=clip, transcript_text=slice_text, candidate=candidate)
        dual_meta = DualPlatformMetadata(youtube=yt_meta, instagram=ig_meta)

        # 1. Generate Base Title & Description for backward compatibility
        generated_title = yt_meta.title or self._synthesize_title(hook=hook, topic=topic_cue, slice_text=slice_text)
        generated_desc = yt_meta.description or self._synthesize_description(hook=hook, slice_text=slice_text, topic=topic_cue)
        generated_hashtags = yt_meta.hashtags or self._synthesize_hashtags(slice_text=slice_text)
        generated_mentions = ig_meta.mentions or list(self.reqs.required_mentions)
        generated_cta = yt_meta.cta or self._synthesize_cta(topic=topic_cue)

        # Evaluate Quality Gate
        gate_res = self.quality_gate.evaluate(
            title=generated_title,
            description=generated_desc,
            hashtags=generated_hashtags,
            mentions=generated_mentions,
            cta=generated_cta,
        )

        overall_compliance = "SEO_PASS" if (yt_meta.is_compliant and ig_meta.is_compliant) else (
            "SEO_WARN" if (yt_meta.compliance_score >= 60 and ig_meta.compliance_score >= 60) else "SEO_REJECT"
        )
        avg_score = round((yt_meta.compliance_score + ig_meta.compliance_score) / 2.0, 1)

        repaired = False
        if self.reqs.prohibited_terms and any(pt.strip() and pt.lower() in (hook + " " + topic_cue + " " + slice_text).lower() for pt in self.reqs.prohibited_terms):
            repaired = True
        if not gate_res.is_compliant:
            repaired = True

        compliance_record = {
            "passed": (overall_compliance in ("SEO_PASS", "SEO_WARN")),
            "status": overall_compliance,
            "score": avg_score,
            "title": generated_title,
            "caption": ig_meta.caption,
            "description": generated_desc,
            "tags": generated_hashtags,
            "hashtags": generated_hashtags,
            "cta": generated_cta,
            "mentions": generated_mentions,
            "violations": yt_meta.errors + ig_meta.errors,
            "warnings": yt_meta.warnings + ig_meta.warnings,
            "matched_requirements": gate_res.matched_requirements,
            "repaired": repaired,
        }

        record = ClipMetadataRecord(
            id=existing.id if existing else new_id(),
            job_id=clip.job_id,
            clip_id=clip.id,
            generated_title=generated_title,
            final_title=generated_title,
            generated_description=generated_desc,
            final_description=generated_desc,
            generated_hashtags=generated_hashtags,
            final_hashtags=generated_hashtags,
            generated_mentions=generated_mentions,
            final_mentions=generated_mentions,
            generated_cta=generated_cta,
            final_cta=generated_cta,
            campaign_requirements_matched=gate_res.matched_requirements,
            compliance_status=overall_compliance,
            compliance_score=avg_score,
            validation_errors=yt_meta.errors + ig_meta.errors,
            validation_warnings=yt_meta.warnings + ig_meta.warnings,
            version=1,
            telemetry={
                "generated_at": utcnow(),
                "rank": clip.rank,
                "score": clip.score,
                "campaign_compliance": compliance_record,
                "youtube": yt_meta.to_dict(),
                "instagram": ig_meta.to_dict(),
                "dual_platform_metadata": dual_meta.to_dict(),
                "youtube_compliance_score": yt_meta.compliance_score,
                "youtube_optimization_score": yt_meta.optimization_score,
                "instagram_compliance_score": ig_meta.compliance_score,
                "instagram_optimization_score": ig_meta.optimization_score,
            },
            created_at=existing.created_at if existing else utcnow(),
            updated_at=utcnow(),
        )

        return store.create_clip_metadata(record)

    def _synthesize_title(self, hook: str, topic: str, slice_text: str) -> str:
        """Synthesizes a compelling, high-CTR viral title (<=100 chars, optimal 45-75 chars)
        following ShortGPT / Hormozi short-form hook patterns and obeying campaign constraints."""
        candidate_title = ""
        clean_hook = hook.strip().rstrip(".!?,;: ")
        clean_topic = topic.strip().rstrip(".!?,;: ")

        if self.reqs.title_patterns:
            pat = self.reqs.title_patterns[0]
            candidate_title = pat.replace("{hook}", clean_hook).replace("{topic}", clean_topic)
            if "{" in candidate_title:
                candidate_title = clean_hook or clean_topic

        if not candidate_title:
            # 1. Clean out conversational verbal filler from the spoken hook
            conversational_prefixes = [
                r"^(so|well|like|honestly|i mean|i think that|you know what i mean|what you have to understand is|at the end of the day|when it comes to|the thing is)\b[\s,]*",
                r"^(today we are going to look at|in this video we are going to discuss|let me tell you about|here is what happened when)\b[\s,]*",
            ]
            scrubbed_hook = clean_hook
            for cp in conversational_prefixes:
                scrubbed_hook = re.sub(cp, "", scrubbed_hook, flags=re.IGNORECASE).strip()

            # 2. Check if the hook already has strong viral triggers or questions
            has_strong_trigger = any(
                tw in scrubbed_hook.lower()
                for tw in ("why", "how to", "mistake", "never", "secret", "truth", "rule", "stop", "fail", "avoid", "vs", "exposed", "warning")
            ) or ("?" in clean_hook or "!" in clean_hook)

            if scrubbed_hook and len(scrubbed_hook) >= 12 and has_strong_trigger:
                candidate_title = scrubbed_hook.title() if scrubbed_hook.islower() else scrubbed_hook
            elif scrubbed_hook and len(scrubbed_hook) >= 12:
                # Use ShortGPT Curiosity / Truth hook formula with the extracted hook essence
                if len(scrubbed_hook) <= 50 and not scrubbed_hook.lower().startswith(("the", "why", "how")):
                    candidate_title = f"The Truth About {scrubbed_hook}"
                else:
                    candidate_title = scrubbed_hook.title() if scrubbed_hook.islower() else scrubbed_hook
            else:
                # Use topic with high-CTR ShortGPT formula
                resolved_topic = clean_topic or "This Essential Rule"
                if any(tw in resolved_topic.lower() for tw in ("highlight", "clip", "key insight")):
                    key_words = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", slice_text[:120])
                    if key_words:
                        resolved_topic = key_words[0]
                    else:
                        resolved_topic = clean_topic

                candidate_title = f"The Biggest {resolved_topic} Mistake"

        # Incorporate brand if specified and fits
        if self.reqs.brand_name and self.reqs.brand_name.lower() not in candidate_title.lower():
            if len(candidate_title) + len(self.reqs.brand_name) + 3 <= self.reqs.max_title_length:
                candidate_title = f"{candidate_title} | {self.reqs.brand_name}"

        # If mandatory phrase is required, ensure it's in the title if it fits and is a real content phrase
        for phrase in self.reqs.required_phrases:
            if phrase.lower() not in candidate_title.lower():
                if len(candidate_title) + len(phrase) + 3 <= self.reqs.max_title_length and (len(phrase) > 8 or " " in phrase):
                    candidate_title = f"{candidate_title} - {phrase}"
                    break

        from .sanitizer import sanitize_public_text
        candidate_title = sanitize_public_text(candidate_title, is_title=True)

        for term in self.reqs.prohibited_terms:
            if term.lower() in candidate_title.lower():
                candidate_title = re.sub(rf"\b{re.escape(term)}\b", "", candidate_title, flags=re.IGNORECASE).strip()

        if len(candidate_title) > self.reqs.max_title_length:
            candidate_title = candidate_title[:self.reqs.max_title_length].rsplit(" ", 1)[0]

        candidate_title = sanitize_public_text(candidate_title, is_title=True)
        return candidate_title or "Key Insight & Breakdown"

    def _synthesize_description(self, hook: str, slice_text: str, topic: str) -> str:
        """Synthesizes an informative, campaign-compliant description formatted for
        high engagement and search ranking using the MoneyPrinterTurbo platform structure."""
        parts: list[str] = []

        # 1. Attention-grabbing summary hook
        summary = _extract_coherent_sentences(slice_text, min_chars=30, max_chars=220)
        if not summary:
            clean_h = re.sub(r"\.{2,}", "", hook).strip().rstrip(".!?,")
            summary = clean_h if clean_h else f"An essential breakdown on {topic}."
            if not summary.endswith("."):
                summary += "."
        parts.append(summary)

        # 2. Key Highlights / Takeaways (clean bullet formatting)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", slice_text) if len(s.strip()) > 25]
        if len(sentences) >= 2:
            bullet_points = [f"• {s.rstrip('.')}." for s in sentences[1:3]]
            parts.append("📌 Key Highlights:\n" + "\n".join(bullet_points))

        # Include genuine campaign topics/phrases naturally (avoiding SOP words)
        content_phrases = [
            p for p in self.reqs.required_phrases
            if len(p) > 3 and not any(w in p.lower() for w in ("rejection", "payout", "submit", "tier-1", "late", "must"))
        ]
        if content_phrases:
            phrases_line = "Key Focus: " + ", ".join(content_phrases)
            parts.append(phrases_line)

        # 3. Call to Action line
        cta_text = self._synthesize_cta(topic=topic)
        if cta_text:
            parts.append(f"👉 {cta_text}")

        # 4. Campaign URL
        if self.reqs.campaign_url:
            parts.append(f"🔗 Learn more: {self.reqs.campaign_url.strip()}")

        # 5. Featuring / Creator mentions
        if self.reqs.required_mentions:
            parts.append("Featuring: " + " ".join(self.reqs.required_mentions))

        if self.reqs.description_guidelines:
            for dg in self.reqs.description_guidelines:
                clean_dg = dg.strip().strip('“"”')
                if len(clean_dg) > 10 and clean_dg not in parts:
                    parts.append(clean_dg)

        # 6. High-volume Hashtags (Campaign tags first, then platform tags)
        if self.reqs.required_hashtags:
            norm_tags = [h if h.startswith("#") else f"#{h}" for h in self.reqs.required_hashtags if "alamr" not in h.lower()]
            if norm_tags:
                parts.append(" ".join(norm_tags))

        desc = "\n\n".join(parts)
        if len(desc) > self.reqs.max_description_length:
            desc = desc[:self.reqs.max_description_length].rsplit(" ", 1)[0]
        from .sanitizer import sanitize_public_text
        return sanitize_public_text(desc, is_title=False)

    def _synthesize_hashtags(self, slice_text: str) -> list[str]:
        """Synthesizes deduplicated hashtags starting with campaign-required hashtags."""
        tags: list[str] = [t for t in self.reqs.required_hashtags if "alamr" not in t.lower()]
        clean_tags_lower = [t.lower().lstrip("#") for t in tags]

        words = re.findall(r"\b[A-Za-z]{4,15}\b", slice_text)
        stopwords = {
            "this", "that", "with", "from", "have", "they", "will", "what", "when",
            "there", "about", "your", "more", "into", "their", "rejection", "submit",
            "payouts", "late", "must", "consistent", "required", "think", "start", "like",
        }
        prohibited_set = {p.lower().strip() for p in self.reqs.prohibited_terms}

        candidate_tags = ["#Shorts", "#Reels", "#Viral", "#Founders", "#Mindset", "#Success"]
        if self.reqs.brand_name and "alamr" not in self.reqs.brand_name.lower():
            b_tag = "#" + re.sub(r"[^\w]", "", self.reqs.brand_name)
            if b_tag.lower() not in [t.lower() for t in candidate_tags] and b_tag.lower().lstrip("#") not in clean_tags_lower:
                candidate_tags.insert(0, b_tag)

        for w in words:
            wl = w.lower()
            if wl not in stopwords and wl not in prohibited_set and wl not in clean_tags_lower and "alamr" not in wl and "autoclip" not in wl:
                candidate_tags.append(f"#{w.capitalize()}")
                clean_tags_lower.append(wl)
                if len(candidate_tags) >= 8:
                    break

        for t in candidate_tags:
            if t.lower().lstrip("#") not in [x.lower().lstrip("#") for x in tags]:
                tags.append(t)
            if len(tags) >= 7:
                break

        return tags

    def _synthesize_cta(self, topic: str) -> str:
        """Synthesizes CTA honoring campaign instructions."""
        if self.reqs.cta_instructions:
            return self.reqs.cta_instructions[0].strip()
        if self.reqs.cta_required:
            return "👉 Follow for more actionable clips and share your perspective below!"
        return "Subscribe for more insights."

    def update_operator_metadata(
        self,
        clip_id: str,
        final_title: str | None = None,
        final_description: str | None = None,
        final_hashtags: list[str] | None = None,
        final_mentions: list[str] | None = None,
        final_cta: str | None = None,
        reset_to_generated: bool = False,
    ) -> ClipMetadataRecord:
        """Applies operator edits, increments version, tracks history, and evaluates quality gate."""
        record = store.get_clip_metadata(clip_id)
        if not record:
            raise ValueError(f"No metadata found for clip '{clip_id}'.")

        history = record.telemetry.get("history", [])
        snapshot = {
            "version": record.version,
            "final_title": record.final_title,
            "final_description": record.final_description,
            "final_hashtags": record.final_hashtags,
            "final_mentions": record.final_mentions,
            "final_cta": record.final_cta,
            "compliance_status": record.compliance_status,
            "updated_at": utcnow(),
        }
        history.append(snapshot)
        record.telemetry["history"] = history

        if reset_to_generated:
            record.final_title = record.generated_title
            record.final_description = record.generated_description
            record.final_hashtags = list(record.generated_hashtags)
            record.final_mentions = list(record.generated_mentions)
            record.final_cta = record.generated_cta
        else:
            if final_title is not None:
                record.final_title = final_title
            if final_description is not None:
                record.final_description = final_description
            if final_hashtags is not None:
                record.final_hashtags = [h if h.startswith("#") else f"#{h}" for h in final_hashtags if h.strip()]
            if final_mentions is not None:
                record.final_mentions = [m if m.startswith("@") else f"@{m}" for m in final_mentions if m.strip()]
            if final_cta is not None:
                record.final_cta = final_cta

        record.version += 1
        record.updated_at = utcnow()

        gate_res = self.quality_gate.evaluate(
            title=record.final_title,
            description=record.final_description,
            hashtags=record.final_hashtags,
            mentions=record.final_mentions,
            cta=record.final_cta,
        )
        record.compliance_status = gate_res.status.value
        record.compliance_score = gate_res.score
        record.validation_errors = gate_res.errors
        record.validation_warnings = gate_res.warnings
        record.campaign_requirements_matched = gate_res.matched_requirements

        updated = store.create_clip_metadata(record)
        log.info(
            "Updated operator metadata for clip %s to v%d (status=%s, score=%.1f)",
            clip_id, updated.version, updated.compliance_status, updated.compliance_score
        )
        return updated
