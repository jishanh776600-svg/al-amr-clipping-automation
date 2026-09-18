"""Production-grade SEO engine for AL AMR clipping automation."""

from __future__ import annotations

import logging
import re
from typing import Any

from autoclip.campaign.models_intelligence import CampaignSpecification
from autoclip.db.models import Clip, ClipCandidateRecord, ClipMetadataRecord, new_id, utcnow
from autoclip.db import store
from .extractor import extract_campaign_seo_requirements
from .models import CampaignSEORequirements, ComplianceResult, ComplianceStatus
from .quality_gate import MetadataQualityGate

log = logging.getLogger(__name__)


class SEOEngine:
    """Generates, validates, and manages per-short SEO and publishing metadata."""

    def __init__(self, requirements: CampaignSEORequirements | None = None) -> None:
        self.reqs = requirements or CampaignSEORequirements()
        self.quality_gate = MetadataQualityGate(self.reqs)

    @classmethod
    def from_campaign_spec(cls, campaign_spec: CampaignSpecification | None) -> SEOEngine:
        reqs = extract_campaign_seo_requirements(campaign_spec)
        return cls(requirements=reqs)

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

        # 1. Generate Clip-Specific Title
        generated_title = self._synthesize_title(hook=hook, topic=topic_cue, slice_text=slice_text)

        # 2. Generate Clip-Specific Description
        generated_desc = self._synthesize_description(
            hook=hook,
            slice_text=slice_text,
            topic=topic_cue,
        )

        # 3. Generate Hashtags
        generated_hashtags = self._synthesize_hashtags(slice_text=slice_text)

        # 4. Generate Mentions
        generated_mentions = list(self.reqs.required_mentions)

        # 5. Generate CTA
        generated_cta = self._synthesize_cta(topic=topic_cue)

        # 6. Evaluate Quality Gate & Deterministic Repair Loop
        gate_res = self.quality_gate.evaluate(
            title=generated_title,
            description=generated_desc,
            hashtags=generated_hashtags,
            mentions=generated_mentions,
            cta=generated_cta,
        )

        repaired = False
        if not gate_res.is_compliant:
            repaired = True
            log.info("Repairing SEO metadata violations for clip %s: %s", clip.id, gate_res.errors)

            # Repair hashtags: ensure all required campaign hashtags are present at the beginning
            for rh in self.reqs.required_hashtags:
                norm_rh = rh if rh.startswith("#") else f"#{rh}"
                if norm_rh.lower() not in [h.lower() for h in generated_hashtags]:
                    generated_hashtags.insert(0, norm_rh)

            # Repair mentions: ensure all required mentions are in mentions list and description
            for rm in self.reqs.required_mentions:
                norm_rm = rm if rm.startswith("@") else f"@{rm}"
                if norm_rm.lower() not in [m.lower() for m in generated_mentions]:
                    generated_mentions.append(norm_rm)
                if norm_rm.lower() not in generated_desc.lower():
                    generated_desc += f"\n\nFeaturing {norm_rm}"

            # Repair CTA: ensure mandatory CTA is in CTA and description
            if self.reqs.cta_required and not gate_res.matched_requirements.get("cta_satisfied"):
                cta_choice = self.reqs.cta_instructions[0] if self.reqs.cta_instructions else "👉 Follow for more and comment below!"
                generated_cta = cta_choice
                if cta_choice.lower() not in generated_desc.lower():
                    generated_desc += f"\n\n{cta_choice}"

            # Repair required phrases: ensure key campaign phrases appear in description or title
            for rp in self.reqs.required_phrases:
                if rp.lower() not in generated_title.lower() and rp.lower() not in generated_desc.lower():
                    generated_desc += f"\n\nFocus: {rp}"

            # Repair URL: ensure campaign URL is present
            if self.reqs.campaign_url and not gate_res.matched_requirements.get("url_satisfied"):
                if self.reqs.campaign_url.lower() not in generated_desc.lower():
                    generated_desc += f"\n\n🔗 {self.reqs.campaign_url.strip()}"

            # Repair prohibited terms: scrub from title, description, and hashtags
            for pt in self.reqs.prohibited_terms:
                pt_clean = pt.strip()
                if not pt_clean:
                    continue
                generated_title = re.sub(rf"\b{re.escape(pt_clean)}\b", "", generated_title, flags=re.IGNORECASE).strip()
                generated_desc = re.sub(rf"\b{re.escape(pt_clean)}\b", "", generated_desc, flags=re.IGNORECASE).strip()
                generated_hashtags = [h for h in generated_hashtags if pt_clean.lower() not in h.lower()]

            # Re-evaluate after repair
            gate_res = self.quality_gate.evaluate(
                title=generated_title,
                description=generated_desc,
                hashtags=generated_hashtags,
                mentions=generated_mentions,
                cta=generated_cta,
            )
            log.info("After repair loop: status=%s, score=%.1f, errors=%s", gate_res.status.value, gate_res.score, gate_res.errors)

        compliance_record = {
            "passed": gate_res.is_compliant,
            "status": gate_res.status.value,
            "score": gate_res.score,
            "title": generated_title,
            "caption": generated_title,
            "description": generated_desc,
            "tags": generated_hashtags,
            "hashtags": generated_hashtags,
            "cta": generated_cta,
            "mentions": generated_mentions,
            "violations": gate_res.errors,
            "warnings": gate_res.warnings,
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
            compliance_status=gate_res.status.value,
            compliance_score=gate_res.score,
            validation_errors=gate_res.errors,
            validation_warnings=gate_res.warnings,
            version=1,
            telemetry={
                "generated_at": utcnow(),
                "rank": clip.rank,
                "score": clip.score,
                "campaign_compliance": compliance_record,
            },
            created_at=existing.created_at if existing else utcnow(),
            updated_at=utcnow(),
        )

        return store.create_clip_metadata(record)

    def _synthesize_title(self, hook: str, topic: str, slice_text: str) -> str:
        """Synthesizes a compelling, concise title (<=100 chars) obeying campaign patterns and keywords."""
        candidate_title = ""
        if self.reqs.title_patterns:
            pat = self.reqs.title_patterns[0]
            clean_hook = hook.strip().rstrip(".!?,")
            clean_topic = topic.strip()
            candidate_title = pat.replace("{hook}", clean_hook).replace("{topic}", clean_topic)
            if "{" in candidate_title:
                candidate_title = clean_hook or clean_topic

        if not candidate_title:
            if hook and len(hook) > 10:
                clean_h = hook.strip().rstrip(".!?,")
                candidate_title = clean_h
            else:
                candidate_title = topic.strip()

        # Incorporate brand if specified and fits
        if self.reqs.brand_name and self.reqs.brand_name.lower() not in candidate_title.lower():
            if len(candidate_title) + len(self.reqs.brand_name) + 3 <= self.reqs.max_title_length:
                candidate_title = f"{candidate_title} | {self.reqs.brand_name}"

        # If mandatory phrase is required, ensure it's in the title if it fits
        for phrase in self.reqs.required_phrases:
            if phrase.lower() not in candidate_title.lower():
                if len(candidate_title) + len(phrase) + 3 <= self.reqs.max_title_length:
                    candidate_title = f"{candidate_title} - {phrase}"
                    break

        # Filter prohibited terms
        for term in self.reqs.prohibited_terms:
            if term.lower() in candidate_title.lower():
                candidate_title = re.sub(rf"\b{re.escape(term)}\b", "", candidate_title, flags=re.IGNORECASE).strip()

        if len(candidate_title) > self.reqs.max_title_length:
            candidate_title = candidate_title[:self.reqs.max_title_length - 3] + "..."

        return candidate_title or "AL AMR Insight"

    def _synthesize_description(self, hook: str, slice_text: str, topic: str) -> str:
        """Synthesizes an informative, campaign-compliant description."""
        parts: list[str] = []

        summary = hook.strip() if hook else f"An essential moment discussing {topic}."
        if slice_text and len(slice_text) > 40:
            snippet = slice_text.strip()
            first_period = snippet.find(".", 40)
            if first_period != -1 and first_period < 200:
                summary = snippet[:first_period + 1].strip()
        parts.append(summary)

        if self.reqs.required_phrases:
            phrases_line = "Key Focus: " + ", ".join(self.reqs.required_phrases)
            parts.append(phrases_line)

        cta_text = self._synthesize_cta(topic=topic)
        if cta_text:
            parts.append(cta_text)

        if self.reqs.campaign_url:
            parts.append(f"🔗 Learn more: {self.reqs.campaign_url.strip()}")

        if self.reqs.required_mentions:
            parts.append("Featuring: " + " ".join(self.reqs.required_mentions))

        if self.reqs.description_guidelines:
            for dg in self.reqs.description_guidelines:
                clean_dg = dg.strip()
                if clean_dg and clean_dg not in parts:
                    parts.append(clean_dg)

        desc = "\n\n".join(parts)
        if len(desc) > self.reqs.max_description_length:
            desc = desc[:self.reqs.max_description_length - 3] + "..."
        return desc

    def _synthesize_hashtags(self, slice_text: str) -> list[str]:
        """Synthesizes deduplicated hashtags starting with campaign-required hashtags."""
        tags: list[str] = list(self.reqs.required_hashtags)
        clean_tags_lower = [t.lower().lstrip("#") for t in tags]

        words = re.findall(r"\b[A-Za-z]{4,15}\b", slice_text)
        stopwords = {"this", "that", "with", "from", "have", "they", "will", "what", "when", "there", "about", "your", "more", "into", "their"}
        prohibited_set = {p.lower().strip() for p in self.reqs.prohibited_terms}

        candidate_tags = ["#Shorts", "#Reels", "#Viral", "#ALAMR"]
        for w in words:
            wl = w.lower()
            if wl not in stopwords and wl not in prohibited_set and wl not in clean_tags_lower:
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
