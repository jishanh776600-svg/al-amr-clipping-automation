"""Deterministic MetadataQualityGate validating campaign compliance, safety, and platform constraints."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .models import CampaignSEORequirements, ComplianceResult, ComplianceStatus


class MetadataQualityGate:
    """Deterministic validator enforcing campaign compliance on metadata packages."""

    def __init__(self, requirements: CampaignSEORequirements | None = None) -> None:
        self.reqs = requirements or CampaignSEORequirements()

    def evaluate(
        self,
        title: str,
        description: str,
        hashtags: list[str],
        mentions: list[str],
        cta: str = "",
    ) -> ComplianceResult:
        errors: list[str] = []
        warnings: list[str] = []
        matched_reqs: dict[str, Any] = {
            "matched_phrases": [],
            "matched_mentions": [],
            "matched_hashtags": [],
            "cta_satisfied": False,
            "url_satisfied": False,
        }

        full_corpus = f"{title}\n{description}\n{' '.join(hashtags)}\n{' '.join(mentions)}\n{cta}".lower()

        # 1. Prohibited terms validation (Hard violation)
        for term in self.reqs.prohibited_terms:
            t = term.lower().strip()
            if not t:
                continue
            pattern = rf"\b{re.escape(t)}\b" if re.match(r"^\w+$", t) else re.escape(t)
            if re.search(pattern, full_corpus):
                errors.append(f"Prohibited term '{term}' detected in metadata.")

        # 2. Mandatory campaign phrases
        for phrase in self.reqs.required_phrases:
            p = phrase.lower().strip()
            if not p:
                continue
            if p in full_corpus:
                matched_reqs["matched_phrases"].append(phrase)
            else:
                errors.append(f"Required campaign phrase '{phrase}' is missing from metadata.")

        # 3. Required mentions
        normalized_mentions_in_content = [m.lower().lstrip("@") for m in mentions]
        desc_mentions = [m.lower().lstrip("@") for m in re.findall(r"@[\w\.-]+", description)]
        all_present_mentions = set(normalized_mentions_in_content + desc_mentions)

        for req_m in self.reqs.required_mentions:
            clean_m = req_m.lower().lstrip("@")
            if clean_m in all_present_mentions:
                matched_reqs["matched_mentions"].append(req_m)
            else:
                errors.append(f"Required campaign mention '{req_m}' is missing.")

        # 4. Required hashtags
        normalized_tags = [h.lower().lstrip("#") for h in hashtags]
        desc_tags = [h.lower().lstrip("#") for h in re.findall(r"#[\w]+", description)]
        all_present_tags = set(normalized_tags + desc_tags)

        for req_h in self.reqs.required_hashtags:
            clean_h = req_h.lower().lstrip("#")
            if clean_h in all_present_tags:
                matched_reqs["matched_hashtags"].append(req_h)
            else:
                errors.append(f"Required campaign hashtag '{req_h}' is missing.")

        # 5. CTA validation
        if self.reqs.cta_required:
            has_cta = bool(cta.strip() or ("http" in description) or any(
                keyword in description.lower() for keyword in ["link", "click", "check out", "follow", "subscribe", "watch", "visit"]
            ))
            if has_cta:
                matched_reqs["cta_satisfied"] = True
            else:
                errors.append("Campaign requires a Call To Action (CTA), but none was provided.")
        else:
            matched_reqs["cta_satisfied"] = True

        # 6. URL validation
        if self.reqs.campaign_url:
            raw_url = self.reqs.campaign_url.strip()
            if raw_url.lower() in full_corpus:
                parsed = urllib.parse.urlparse(raw_url)
                if parsed.scheme in ("http", "https") and parsed.netloc:
                    matched_reqs["url_satisfied"] = True
                else:
                    errors.append(f"Campaign URL '{raw_url}' is malformed or missing scheme.")
            else:
                errors.append(f"Required campaign URL '{raw_url}' is missing from metadata description.")
        else:
            matched_reqs["url_satisfied"] = True

        # 7. Title length & structure
        title_stripped = title.strip()
        if not title_stripped:
            errors.append("Title cannot be empty.")
        elif len(title_stripped) < self.reqs.min_title_length:
            warnings.append(f"Title is very short ({len(title_stripped)} chars; minimum recommended is {self.reqs.min_title_length}).")
        elif len(title_stripped) > self.reqs.max_title_length:
            errors.append(f"Title exceeds maximum length of {self.reqs.max_title_length} characters ({len(title_stripped)} chars).")

        # 8. Description length & structure
        desc_stripped = description.strip()
        if not desc_stripped:
            errors.append("Description cannot be empty.")
        elif len(desc_stripped) > self.reqs.max_description_length:
            errors.append(f"Description exceeds maximum length of {self.reqs.max_description_length} characters ({len(desc_stripped)} chars).")

        found_urls = re.findall(r"(https?://\S+)", description)
        for url in found_urls:
            parsed = urllib.parse.urlparse(url)
            if not parsed.scheme or not parsed.netloc or "<" in url or ">" in url:
                errors.append(f"Malformed or unsafe link found in description: '{url}'.")

        # 9. Calculate score and status
        score = 100.0
        score -= len(errors) * 35.0
        score -= len(warnings) * 10.0
        score = max(0.0, min(100.0, round(score, 1)))

        if errors:
            status = ComplianceStatus.SEO_REJECT
        elif warnings:
            status = ComplianceStatus.SEO_WARN
        else:
            status = ComplianceStatus.SEO_PASS

        return ComplianceResult(
            status=status,
            score=score,
            errors=errors,
            warnings=warnings,
            matched_requirements=matched_reqs,
        )
