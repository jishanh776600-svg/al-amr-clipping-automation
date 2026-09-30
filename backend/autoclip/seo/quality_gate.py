"""Deterministic MetadataQualityGate validating campaign compliance, safety, and platform constraints."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .models import (
    CampaignSEORequirements,
    CampaignSEOSpec,
    ComplianceResult,
    ComplianceStatus,
    InstagramMetadata,
    YouTubeMetadata,
)
from .sanitizer import detect_internal_leakage, sanitize_public_text


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

        # 0. Zero internal pipeline leakage check (Hard violation)
        leak_errors = detect_internal_leakage(f"{title}\n{description}\n{' '.join(hashtags)}\n{' '.join(mentions)}\n{cta}")
        if leak_errors:
            errors.extend(leak_errors)

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


def validate_youtube_metadata(
    metadata: YouTubeMetadata,
    spec: CampaignSEOSpec | None = None,
    transcript: str = "",
) -> YouTubeMetadata:
    """Deterministically validates YouTube Shorts metadata against campaign rules and YouTube constraints."""
    errors: list[str] = []
    warnings: list[str] = []
    rule_evals: dict[str, bool] = {}

    title = (metadata.title or "").strip()
    description = (metadata.description or "").strip()
    hashtags = metadata.hashtags or []
    tags = metadata.tags or []

    # 0. Zero internal pipeline leakage check (Hard violation)
    leak_errors = detect_internal_leakage(f"{title}\n{description}\n{' '.join(hashtags)}\n{' '.join(tags)}")
    if leak_errors:
        errors.extend(leak_errors)
        rule_evals["zero_leakage_pass"] = False
    else:
        rule_evals["zero_leakage_pass"] = True

    # 1. Prohibited terms (from global + youtube rules)
    prohibited = set()
    if spec:
        prohibited.update(spec.global_rules.prohibited_terms)
        prohibited.update(spec.youtube_rules.prohibited_terms)

    full_text = f"{title}\n{description}\n{' '.join(hashtags)}\n{' '.join(tags)}".lower()
    prohibited_found = False
    for pt in prohibited:
        p = pt.strip().lower()
        if p and (re.search(rf"\b{re.escape(p)}\b", full_text) if re.match(r"^\w+$", p) else p in full_text):
            errors.append(f"Prohibited term '{pt}' found in YouTube metadata.")
            prohibited_found = True
    rule_evals["prohibited_terms_pass"] = not prohibited_found

    # 2. Title length hard check: YouTube allows max 100 characters
    if not title:
        errors.append("YouTube title cannot be empty.")
        rule_evals["title_present"] = False
        rule_evals["title_length_limit"] = False
    elif len(title) > 100:
        errors.append(f"YouTube title exceeds maximum 100 characters ({len(title)} chars).")
        rule_evals["title_present"] = True
        rule_evals["title_length_limit"] = False
    else:
        rule_evals["title_present"] = True
        rule_evals["title_length_limit"] = True

    # 3. Required phrases
    req_phrases: list[str] = []
    if spec:
        req_phrases.extend(spec.global_rules.required_terms)
        req_phrases.extend(spec.youtube_rules.required_phrases)

    missing_phrases = []
    for rp in req_phrases:
        if rp.strip().lower() not in full_text:
            missing_phrases.append(rp)
    if missing_phrases:
        errors.append(f"Required campaign phrases missing from YouTube metadata: {', '.join(missing_phrases)}")
        rule_evals["required_phrases_pass"] = False
    else:
        rule_evals["required_phrases_pass"] = True

    # 4. Links validation
    urls = re.findall(r"(https?://\S+)", description)
    malformed = False
    for u in urls:
        parsed = urllib.parse.urlparse(u)
        if not parsed.scheme or not parsed.netloc or "<" in u or ">" in u:
            errors.append(f"Malformed YouTube URL: '{u}'")
            malformed = True
    rule_evals["urls_valid"] = not malformed

    # 5. Compliance score
    comp_score = 100.0 - (len(errors) * 40.0)
    metadata.compliance_score = max(0.0, min(100.0, round(comp_score, 1)))
    metadata.compliance_status = "PASS" if not errors else "FAIL"

    # 6. Optimization score (discoverability, mobile display, hook strength)
    opt_score = 100.0
    # Sweet spot for Shorts title is 25-75 chars
    if title and (len(title) < 20 or len(title) > 85):
        opt_score -= 10.0
        warnings.append(f"YouTube title length ({len(title)} chars) is outside optimal 20-85 range.")
        rule_evals["optimal_title_length"] = False
    else:
        rule_evals["optimal_title_length"] = True

    # Check for #Shorts tag in title or description
    has_shorts_tag = "#shorts" in full_text or any(h.lower() == "#shorts" for h in hashtags)
    if not has_shorts_tag:
        opt_score -= 15.0
        warnings.append("Recommended '#Shorts' hashtag missing from title/description.")
        rule_evals["shorts_tag_present"] = False
    else:
        rule_evals["shorts_tag_present"] = True

    # Description structure: summary + cta
    if not description:
        opt_score -= 20.0
        warnings.append("YouTube description is empty.")
        rule_evals["description_structured"] = False
    elif len(description) < 30:
        opt_score -= 10.0
        warnings.append("YouTube description is very brief (<30 chars).")
        rule_evals["description_structured"] = False
    else:
        rule_evals["description_structured"] = True

    # CTA presence
    if not metadata.cta and not any(kw in description.lower() for kw in ["subscribe", "follow", "comment", "check out", "link"]):
        opt_score -= 10.0
        warnings.append("No clear viewer call-to-action found in YouTube metadata.")
        rule_evals["cta_present"] = False
    else:
        rule_evals["cta_present"] = True

    metadata.optimization_score = max(0.0, min(100.0, round(opt_score, 1)))
    metadata.rule_evaluations = rule_evals
    metadata.errors = errors
    metadata.warnings = warnings
    return metadata


def validate_instagram_metadata(
    metadata: InstagramMetadata,
    spec: CampaignSEOSpec | None = None,
    transcript: str = "",
) -> InstagramMetadata:
    """Deterministically validates Instagram Reels metadata against campaign rules and IG constraints."""
    errors: list[str] = []
    warnings: list[str] = []
    rule_evals: dict[str, bool] = {}

    caption = (metadata.caption or "").strip()
    first_line_hook = (metadata.first_line_hook or "").strip()
    hashtags = metadata.hashtags or []
    mentions = metadata.mentions or []

    # 0. Zero internal pipeline leakage check (Hard violation)
    leak_errors = detect_internal_leakage(f"{caption}\n{' '.join(hashtags)}\n{' '.join(mentions)}")
    if leak_errors:
        errors.extend(leak_errors)
        rule_evals["zero_leakage_pass"] = False
    else:
        rule_evals["zero_leakage_pass"] = True

    # 1. Prohibited terms (from global + instagram rules)
    prohibited = set()
    if spec:
        prohibited.update(spec.global_rules.prohibited_terms)
        prohibited.update(spec.instagram_rules.prohibited_terms)

    full_text = f"{caption}\n{' '.join(hashtags)}\n{' '.join(mentions)}".lower()
    prohibited_found = False
    for pt in prohibited:
        p = pt.strip().lower()
        if p and (re.search(rf"\b{re.escape(p)}\b", full_text) if re.match(r"^\w+$", p) else p in full_text):
            errors.append(f"Prohibited term '{pt}' found in Instagram metadata.")
            prohibited_found = True
    rule_evals["prohibited_terms_pass"] = not prohibited_found

    # 2. Caption length hard check: Instagram allows max 2200 characters
    if not caption:
        errors.append("Instagram caption cannot be empty.")
        rule_evals["caption_present"] = False
        rule_evals["caption_length_limit"] = False
    elif len(caption) > 2200:
        errors.append(f"Instagram caption exceeds maximum 2200 characters ({len(caption)} chars).")
        rule_evals["caption_present"] = True
        rule_evals["caption_length_limit"] = False
    else:
        rule_evals["caption_present"] = True
        rule_evals["caption_length_limit"] = True

    # 3. First-line hook validation: Must be <= 125 chars (the fold before '...more')
    if not first_line_hook:
        first_line = caption.split("\n")[0].strip() if caption else ""
        if first_line:
            first_line_hook = first_line
            metadata.first_line_hook = first_line
    if not first_line_hook:
        errors.append("Instagram metadata requires a first-line hook.")
        rule_evals["first_line_hook_present"] = False
    elif len(first_line_hook) > 125:
        warnings.append(f"Instagram first-line hook exceeds 125 characters ({len(first_line_hook)} chars) and may be truncated by '...more'.")
        rule_evals["first_line_hook_present"] = True
    else:
        rule_evals["first_line_hook_present"] = True

    # 4. Required mentions from campaign spec
    req_mentions = []
    if spec and spec.instagram_rules.mention_rules:
        req_mentions.extend(spec.instagram_rules.mention_rules)

    caption_mentions_lower = {m.lower().lstrip("@") for m in re.findall(r"@[\w\.-]+", caption)} | {m.lower().lstrip("@") for m in mentions}
    missing_mentions = []
    for rm in req_mentions:
        clean_rm = rm.lower().lstrip("@")
        if clean_rm and clean_rm not in caption_mentions_lower:
            missing_mentions.append(rm)
    if missing_mentions:
        errors.append(f"Required Instagram account mention '{missing_mentions[0]}' is missing.")
        rule_evals["required_mentions_pass"] = False
    else:
        rule_evals["required_mentions_pass"] = True

    # 5. Required phrases
    req_phrases = []
    if spec:
        req_phrases.extend(spec.global_rules.required_terms)
        req_phrases.extend(spec.instagram_rules.required_phrases)

    missing_phrases = []
    for rp in req_phrases:
        if rp.strip().lower() not in full_text:
            missing_phrases.append(rp)
    if missing_phrases:
        errors.append(f"Required campaign phrases missing from Instagram caption: {', '.join(missing_phrases)}")
        rule_evals["required_phrases_pass"] = False
    else:
        rule_evals["required_phrases_pass"] = True

    # 6. Compliance score
    comp_score = 100.0 - (len(errors) * 40.0)
    metadata.compliance_score = max(0.0, min(100.0, round(comp_score, 1)))
    metadata.compliance_status = "PASS" if not errors else "FAIL"

    # 7. Optimization score (feed retention, line breaks, clean hashtags)
    opt_score = 100.0

    # Line break formatting (not an unbroken text block)
    if "\n" not in caption and len(caption) > 120:
        opt_score -= 15.0
        warnings.append("Instagram caption lacks paragraph line breaks for readability.")
        rule_evals["formatting_readability"] = False
    else:
        rule_evals["formatting_readability"] = True

    # Hashtag count: Sweet spot is 3-8 relevant hashtags, not 30 generic tags
    tag_count = len(hashtags) or len(re.findall(r"#\w+", caption))
    if tag_count < 2:
        opt_score -= 10.0
        warnings.append("Instagram caption has fewer than 2 hashtags.")
        rule_evals["optimal_hashtag_count"] = False
    elif tag_count > 12:
        opt_score -= 10.0
        warnings.append(f"Instagram caption has {tag_count} hashtags (exceeds recommended 3-8).")
        rule_evals["optimal_hashtag_count"] = False
    else:
        rule_evals["optimal_hashtag_count"] = True

    # Bio link CTA (Instagram caption links aren't clickable; should refer to 'link in bio')
    has_bio_cta = any(w in caption.lower() for w in ["link in bio", "bio link", "check bio", "link on profile", "link in profile"])
    if not has_bio_cta and (spec and (spec.instagram_rules.link_rules or spec.global_rules.links)):
        warnings.append("Campaign links should be referenced as 'link in bio' for Instagram Reels.")
        opt_score -= 5.0

    metadata.optimization_score = max(0.0, min(100.0, round(opt_score, 1)))
    metadata.rule_evaluations = rule_evals
    metadata.errors = errors
    metadata.warnings = warnings
    return metadata
