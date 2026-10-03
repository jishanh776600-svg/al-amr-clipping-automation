"""Whop Campaign Guidelines Parser and CampaignBrief Generator (Step 4).

Extracts, normalizes, hashes, and structures campaign guidelines and rules
into a validated CampaignBrief with atomic provenance-tracked rules.
Strictly separates video editing compliance from operational submission instructions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import (
    CampaignRule,
    ParsingStatus,
    RuleCategory,
    WhopCampaignBrief,
    validate_campaign_brief,
)

log = logging.getLogger(__name__)

PARSER_VERSION = "1.0.0"

# Operational instruction indicators (must NOT be treated as video-editing rules)
OPERATIONAL_PATTERNS = [
    re.compile(r"\bjoin campaign\b", re.I),
    re.compile(r"\bsign in\b", re.I),
    re.compile(r"\bconnect account\b", re.I),
    re.compile(r"\bupload proof\b", re.I),
    re.compile(r"\bclaim campaign\b", re.I),
    re.compile(r"\bpayment\b", re.I),
    re.compile(r"\bpayout\b", re.I),
    re.compile(r"\bper\s*(?:1k|1,000|\b)\s*views\b", re.I),
    re.compile(r"\bviews across every approved clip\b", re.I),
    re.compile(r"\bperformance shows up\b", re.I),
    re.compile(r"\bhelp & support\b", re.I),
    re.compile(r"\bbudget\b", re.I),
    re.compile(r"\bup for grabs\b", re.I),
    re.compile(r"\bget approval before publishing\b", re.I),
    re.compile(r"\bsubmit(?:ting|ted)?\b", re.I),
]


def normalize_guideline_content(raw_text: str) -> str:
    """Deterministic normalization algorithm for guideline identity and hashing.
    
    Rules:
    1. Lowercase all characters.
    2. Convert carriage-return linebreaks CRLF (\\r\\n) and newlines to spaces.
    3. Remove non-printable control characters.
    4. Collapse all consecutive whitespace sequences (spaces, tabs, newlines) into a single space.
    5. Strip leading and trailing whitespace.
    This guarantees that cosmetic differences in line wrapping, indentation, or multi-column
    line breaks produce identical semantic identity hashes.
    """
    if not raw_text:
        return ""

    text = raw_text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = "".join(ch for ch in text if ord(ch) >= 32 and ord(ch) != 127)
    text = re.sub(r"\s+", " ", text).lower().strip()
    return text


def compute_guideline_hash(content: str | bytes) -> str:
    """Computes deterministic SHA-256 hex digest of normalized guideline text."""
    if isinstance(content, str):
        normalized = normalize_guideline_content(content)
        content_bytes = normalized.encode("utf-8")
    else:
        content_bytes = content

    return hashlib.sha256(content_bytes).hexdigest()


def fetch_guideline_document(
    source_url: str,
    timeout_seconds: int = 15,
) -> Tuple[Optional[str], str, Optional[str]]:
    """Safely retrieves external guideline document content (Dropbox, PDF, DOCX, Google Docs/Drive).
    
    Returns (extracted_text, document_type, error_reason).
    Read-only, zero side-effects.
    """
    if not source_url:
        return None, "none", "Empty source URL"

    clean_url = source_url.strip()

    def _extract_from_bytes(data: bytes, default_name: str = "document") -> Tuple[Optional[str], str, Optional[str]]:
        try:
            from backend.autoclip.campaign.extractor import extract_text_from_docx, extract_text_from_pdf
        except ImportError:
            from autoclip.campaign.extractor import extract_text_from_docx, extract_text_from_pdf

        if data.startswith(b"%PDF"):
            try:
                text = extract_text_from_pdf(data)
                return text, "pdf", None
            except Exception as e:
                return None, "pdf_error", f"Failed extracting PDF text: {e}"
        elif data.startswith(b"PK\x03\x04"):
            try:
                text = extract_text_from_docx(data)
                return text, "docx", None
            except Exception as e:
                return None, "docx_error", f"Failed extracting DOCX text: {e}"
        else:
            try:
                text = data.decode("utf-8")
                if len(text.strip()) > 20:
                    return text, "text", None
            except Exception:
                pass
            return None, "unknown_binary", "Downloaded bytes are neither PDF nor DOCX nor UTF-8 text"

    # 1. Dropbox link handling: convert dl=0 to dl=1 for direct binary stream
    if "dropbox.com" in clean_url:
        dl_url = clean_url
        if "dl=0" in dl_url:
            dl_url = dl_url.replace("dl=0", "dl=1")
        elif "dl=1" not in dl_url:
            separator = "&" if "?" in dl_url else "?"
            dl_url = f"{dl_url}{separator}dl=1"

        try:
            req = urllib.request.Request(
                dl_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AL-AMR/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                data = resp.read()

            text, dtype, err = _extract_from_bytes(data, "dropbox_file")
            return text, f"dropbox_{dtype}", err
        except Exception as exc:
            return None, "dropbox_error", f"Could not retrieve Dropbox guideline: {exc}"

    # 2. Google Docs export handling
    gdoc_match = re.search(r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)", clean_url)
    if gdoc_match:
        doc_id = gdoc_match.group(1)
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
        try:
            req = urllib.request.Request(
                export_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AL-AMR/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                data = resp.read()
            text = data.decode("utf-8", errors="replace").strip()
            if text and len(text) > 30:
                return text, "google_doc", None
        except Exception as exc:
            log.warning("Could not export Google Doc %s as text: %s", doc_id, exc)

    # 3. Google Drive file handling
    gdrive_file_match = re.search(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)", clean_url)
    if gdrive_file_match:
        file_id = gdrive_file_match.group(1)
        direct_dl = f"https://drive.google.com/uc?export=download&id={file_id}"
        try:
            req = urllib.request.Request(
                direct_dl,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AL-AMR/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                data = resp.read()
            text, dtype, err = _extract_from_bytes(data, f"gdrive_{file_id}")
            return text, f"gdrive_{dtype}", err
        except Exception as exc:
            return None, "gdrive_error", f"Could not retrieve Google Drive file: {exc}"

    # 4. Direct PDF, DOCX, TXT, MD links or local filesystem paths
    local_path = Path(clean_url)
    if local_path.is_file():
        try:
            data = local_path.read_bytes()
            text, dtype, err = _extract_from_bytes(data, local_path.name)
            return text, f"local_{dtype}", err
        except Exception as exc:
            return None, "local_error", f"Could not read local guideline file: {exc}"

    url_lower = clean_url.lower()
    if url_lower.endswith((".pdf", ".docx", ".txt", ".md")) or url_lower.startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(
                clean_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AL-AMR/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                data = resp.read()
            text, dtype, err = _extract_from_bytes(data, "direct_file")
            return text, f"direct_{dtype}", err
        except Exception as exc:
            return None, "direct_error", f"Could not retrieve direct guideline: {exc}"

    # 5. Generic Google Drive folder or unknown scheme
    if "drive.google.com" in clean_url:
        return None, "google_drive", "Google Drive folder reference (media asset source)"

    return None, "unsupported_link", f"Unsupported guideline URL scheme: {clean_url}"


def parse_campaign_guidelines(
    campaign_id: str,
    title: str,
    campaign_url: str,
    raw_text: str = "",
    source_urls: Optional[List[str]] = None,
    guideline_urls: Optional[List[str]] = None,
    payout_raw: str = "",
    cpm: Optional[float] = None,
    supported_platforms: Optional[List[str]] = None,
    download_external: bool = True,
) -> WhopCampaignBrief:
    """Parses available guideline materials into a canonical WhopCampaignBrief.
    
    1. Examines campaign detail text and external documents.
    2. Separates content/editing compliance from operational submission rules.
    3. Assigns platform scopes and provenance references.
    4. Identifies ambiguous rules as INTERPRETATION_REQUIRED.
    5. Computes deterministic SHA-256 hash.
    6. Returns structured WhopCampaignBrief.
    """
    source_urls = source_urls or []
    guideline_urls = guideline_urls or []
    supported_platforms = supported_platforms or ["youtube", "instagram", "tiktok"]

    # Resolve primary guideline document source
    combined_raw_text = raw_text or ""
    source_type = "detail_page"
    source_ref = campaign_url

    # Check external guideline documents
    external_text = None
    if download_external:
        # Check source_urls and guideline_urls for external docs
        candidate_urls = [
            u for u in (guideline_urls + source_urls)
            if any(kw in u.lower() for kw in ("dropbox.com", ".pdf", ".docx", ".txt", ".md", "docs.google.com", "drive.google.com/file"))
        ]
        for u in candidate_urls:
            ext_text, doc_type, err = fetch_guideline_document(u)
            if ext_text and len(ext_text.strip()) > 50:
                external_text = ext_text
                source_type = doc_type
                source_ref = u
                log.info("Successfully retrieved external guideline document from %s (%d chars)", u, len(ext_text))
                break

    if external_text:
        full_guideline_content = f"--- DETAIL PAGE METADATA ---\n{combined_raw_text}\n\n--- DOCUMENT GUIDELINES ({source_ref}) ---\n{external_text}"
    else:
        full_guideline_content = combined_raw_text

    # If completely empty
    if not full_guideline_content.strip():
        return WhopCampaignBrief(
            campaign_id=campaign_id,
            title=title,
            campaign_url=campaign_url,
            payout_raw=payout_raw,
            cpm=cpm,
            supported_platforms=supported_platforms,
            guideline_source_type="none",
            guideline_source_reference="none",
            guideline_hash="",
            parsing_status=ParsingStatus.GUIDELINES_UNAVAILABLE,
            raw_guideline_text="",
            rules=[],
        )

    # Compute deterministic hash
    g_hash = compute_guideline_hash(full_guideline_content)

    # Initialize rule collectors
    rules: List[CampaignRule] = []
    operational_instructions: List[str] = []
    rule_idx = 1

    def add_rule(
        category: RuleCategory,
        text: str,
        normalized_value: Any = None,
        mandatory: bool = False,
        prohibited: bool = False,
        platform: str = "all",
        confidence: str = "explicit",
        status: str = "ACTIVE",
        is_operational: bool = False,
        excerpt: str = "",
    ):
        nonlocal rule_idx
        r_id = f"rule_{campaign_id[:8]}_{rule_idx:03d}"
        rule_idx += 1
        rules.append(
            CampaignRule(
                rule_id=r_id,
                category=category,
                text=text.strip(),
                normalized_value=normalized_value,
                mandatory=mandatory,
                prohibited=prohibited,
                platform=platform,
                source_reference=source_ref,
                source_excerpt=excerpt or text.strip(),
                confidence=confidence,
                status=status,
                is_operational=is_operational,
            )
        )

    # 1. Operational instruction identification
    lines = [line.strip() for line in full_guideline_content.splitlines() if line.strip()]
    content_lines: List[str] = []

    for line in lines:
        is_op = any(pattern.search(line) for pattern in OPERATIONAL_PATTERNS)
        if is_op:
            operational_instructions.append(line)
            # Add as operational submission rule with provenance
            add_rule(
                category=RuleCategory.SUBMISSION,
                text=line,
                normalized_value=None,
                mandatory=False,
                prohibited=False,
                platform="all",
                confidence="explicit",
                status="OPERATIONAL",
                is_operational=True,
            )
        else:
            content_lines.append(line)

    # 2. Extract Duration Requirements
    min_dur: Optional[float] = None
    max_dur: Optional[float] = None
    pref_dur: Optional[float] = None
    exact_dur: Optional[float] = None

    dur_range = re.search(r"(\d+)\s*(?:-|to|and)\s*(\d+)\s*(?:s|sec|seconds)", full_guideline_content, re.I)
    if dur_range:
        d1, d2 = float(dur_range.group(1)), float(dur_range.group(2))
        min_dur = min(d1, d2)
        max_dur = max(d1, d2)
        pref_dur = (min_dur + max_dur) / 2.0
        add_rule(
            category=RuleCategory.DURATION,
            text=dur_range.group(0),
            normalized_value={"min": min_dur, "max": max_dur, "preferred": pref_dur},
            mandatory=True,
            platform="all",
            confidence="explicit",
        )
    else:
        under_match = re.search(r"(?:under|less than|max(?:imum)?)\s*(\d+)\s*(?:s|sec|seconds)", full_guideline_content, re.I)
        if under_match:
            max_dur = float(under_match.group(1))
            min_dur = 15.0
            pref_dur = max_dur * 0.75
            add_rule(
                category=RuleCategory.DURATION,
                text=under_match.group(0),
                normalized_value={"min": min_dur, "max": max_dur, "preferred": pref_dur},
                mandatory=True,
                platform="all",
                confidence="explicit",
            )

        exact_match = re.search(r"(?:exact(?:ly)?|precisely)\s*(\d+)\s*(?:s|sec|seconds)", full_guideline_content, re.I)
        if exact_match:
            exact_dur = float(exact_match.group(1))
            min_dur = exact_dur
            max_dur = exact_dur
            pref_dur = exact_dur
            add_rule(
                category=RuleCategory.DURATION,
                text=exact_match.group(0),
                normalized_value={"exact": exact_dur},
                mandatory=True,
                platform="all",
                confidence="explicit",
            )

    # 3. Link-in-bio & CTA Extraction
    link_in_bio: Optional[str] = None
    bio_match = re.search(r"add link in bio\s*[:\-]?\s*([^\s\n\r]+)", full_guideline_content, re.I)
    if bio_match:
        link_in_bio = bio_match.group(1).strip()
        add_rule(
            category=RuleCategory.PUBLISHING,
            text=f"Add link in bio {link_in_bio}",
            normalized_value=link_in_bio,
            mandatory=True,
            platform="all",
            confidence="explicit",
        )

    # 4. Brand logo & Watermark
    branding_rules: List[str] = []
    logo_watermark_required = False
    if re.search(r"\binclude brand logo\b|\bbrand logo required\b|\bwith logo\b", full_guideline_content, re.I):
        logo_watermark_required = True
        branding_rules.append("Include brand logo")
        add_rule(
            category=RuleCategory.BRANDING,
            text="Include brand logo",
            normalized_value="include_logo",
            mandatory=True,
            platform="all",
            confidence="explicit",
        )

    # 5. Approval Gate
    approval_gate_required = False
    if re.search(r"\bget approval before publishing\b|\bpre-approval required\b", full_guideline_content, re.I):
        approval_gate_required = True
        add_rule(
            category=RuleCategory.PUBLISHING,
            text="Get approval before publishing",
            normalized_value="pre_publish_approval",
            mandatory=True,
            platform="all",
            confidence="explicit",
        )

    # 6. Audio / BGM / SFX Rules
    bgm_rules: List[str] = []
    sfx_rules: List[str] = []
    trending_sound_match = re.search(r"([^\.\n]*trending sounds?[^\.\n]*)", full_guideline_content, re.I)
    if trending_sound_match:
        sound_text = trending_sound_match.group(1).strip()
        bgm_rules.append(sound_text)
        add_rule(
            category=RuleCategory.BGM,
            text=sound_text,
            normalized_value="trending_sounds",
            mandatory=False,
            platform="all",
            confidence="explicit",
        )

    # 7. Hook Rules & Content Guidance
    hook_instructions: List[str] = []
    if re.search(r"\bstrong hooks?\b|\bhook required\b", full_guideline_content, re.I):
        hook_match = re.search(r"([^\.\n]*strong hooks?[^\.\n]*)", full_guideline_content, re.I)
        h_text = hook_match.group(1).strip() if hook_match else "Strong hooks required"
        hook_instructions.append(h_text)
        add_rule(
            category=RuleCategory.EDITING,
            text=h_text,
            normalized_value="strong_hook",
            mandatory=True,
            platform="all",
            confidence="explicit",
        )

    # 7b. Bullet points & numbered list items extraction
    bullet_items = re.findall(r"(?:^|\n)\s*(?:[-*•]|\d+\.)\s+([^\n\r]+)", full_guideline_content)
    for b_item in bullet_items:
        clean_b = b_item.strip()
        if len(clean_b) < 4 or any(op_pat.search(clean_b) for op_pat in OPERATIONAL_PATTERNS):
            continue
        if not any(r.text == clean_b for r in rules):
            cat = RuleCategory.CONTENT
            clean_lower = clean_b.lower()
            if "caption" in clean_lower or "subtitle" in clean_lower:
                cat = RuleCategory.CAPTIONS
            elif "audio" in clean_lower or "sound" in clean_lower or "volume" in clean_lower:
                cat = RuleCategory.AUDIO
            elif "visual" in clean_lower or "frame" in clean_lower or "center" in clean_lower:
                cat = RuleCategory.VISUAL
            elif "duration" in clean_lower or "second" in clean_lower:
                cat = RuleCategory.DURATION
            elif "edit" in clean_lower or "cut" in clean_lower:
                cat = RuleCategory.EDITING

            add_rule(
                category=cat,
                text=clean_b,
                normalized_value=clean_b,
                confidence="explicit",
            )

    # 8. Ambiguous / Subjective Requirements (Flagged INTERPRETATION_REQUIRED)
    ambiguous_patterns = [
        ("engaging", r"([^\.\n]*engaging[^\.\n]*)"),
        ("inspirational", r"([^\.\n]*inspirational[^\.\n]*)"),
        ("viral", r"([^\.\n]*viral short-form[^\.\n]*)"),
        ("accurate messaging", r"([^\.\n]*accurate messaging[^\.\n]*)"),
    ]
    for label, pat in ambiguous_patterns:
        m = re.search(pat, full_guideline_content, re.I)
        if m:
            excerpt = m.group(1).strip()
            if not any(r.text == excerpt for r in rules):
                add_rule(
                    category=RuleCategory.EDITING,
                    text=excerpt,
                    normalized_value=None,  # MUST NOT guess
                    mandatory=False,
                    confidence="inferred",
                    status="INTERPRETATION_REQUIRED",
                    excerpt=excerpt,
                )

    # 9. Prohibited / Banned Content
    banned_words: List[str] = []
    banned_topics: List[str] = []
    prohibit_matches = re.findall(r"\b(?:no|avoid|do not include|forbidden|banned)\s+([a-z0-9\s\-]{3,30})(?:[\.\,\n]|$)", full_guideline_content, re.I)
    for p_term in prohibit_matches:
        term = p_term.strip()
        if term and len(term) >= 3 and term not in ("combat", "payment", "approval"):
            banned_topics.append(term)
            add_rule(
                category=RuleCategory.CONTENT,
                text=f"Prohibited: {term}",
                normalized_value=term,
                mandatory=True,
                prohibited=True,
                platform="all",
                confidence="explicit",
            )

    # 10. Platform-specific Rules
    youtube_reqs: List[str] = []
    instagram_reqs: List[str] = []
    tiktok_reqs: List[str] = []

    for platform_name in ("tiktok", "instagram", "youtube", "shorts", "reels"):
        pat = rf"([^\.\n]*\b{platform_name}\b[^\.\n]*)"
        for match in re.finditer(pat, full_guideline_content, re.I):
            matched_line = match.group(1).strip()
            if any(op_pat.search(matched_line) for op_pat in OPERATIONAL_PATTERNS):
                continue

            target_platform = "youtube" if platform_name in ("youtube", "shorts") else ("instagram" if platform_name in ("instagram", "reels") else "tiktok")
            if target_platform == "youtube" and matched_line not in youtube_reqs:
                youtube_reqs.append(matched_line)
            elif target_platform == "instagram" and matched_line not in instagram_reqs:
                instagram_reqs.append(matched_line)
            elif target_platform == "tiktok" and matched_line not in tiktok_reqs:
                tiktok_reqs.append(matched_line)

            if not any(r.text == matched_line for r in rules):
                add_rule(
                    category=RuleCategory.PUBLISHING,
                    text=matched_line,
                    normalized_value=target_platform,
                    platform=target_platform,
                    confidence="explicit",
                )

    # 11. Mandatory Hashtags Extraction (0% Hallucination)
    extracted_hashtags: List[str] = []
    raw_tags = re.findall(r"(?:^|[\s,;:(])#([A-Za-z0-9_]{2,40})", full_guideline_content)
    for tag in raw_tags:
        # Exclude hex color codes (e.g. #ffffff)
        if re.match(r"^[0-9a-fA-F]{3,6}$", tag) and any(c in tag.lower() for c in "abcdef"):
            continue
        norm_tag = f"#{tag}"
        if norm_tag not in extracted_hashtags:
            extracted_hashtags.append(norm_tag)
            add_rule(
                category=RuleCategory.PUBLISHING,
                text=f"Mandatory Hashtag: {norm_tag}",
                normalized_value=norm_tag,
                mandatory=True,
                platform="all",
                confidence="explicit",
            )

    # 12. Required Account Mentions (0% Hallucination)
    extracted_mentions: List[str] = []
    raw_mentions = re.findall(r"(?:^|[\s,;:(])@([A-Za-z0-9_.-]{2,40})", full_guideline_content)
    for mention in raw_mentions:
        clean_m = mention.rstrip(".")
        norm_m = f"@{clean_m}"
        if norm_m not in extracted_mentions and clean_m.lower() not in ("gmail", "yahoo", "outlook", "example"):
            extracted_mentions.append(norm_m)
            add_rule(
                category=RuleCategory.PUBLISHING,
                text=f"Required Account Mention: {norm_m}",
                normalized_value=norm_m,
                mandatory=True,
                platform="all",
                confidence="explicit",
            )

    # 13. Call-To-Action (CTA) Extraction
    cta_wording = ""
    cta_instructions: List[str] = []
    cta_matches = re.findall(r"(?:cta|call to action|link in bio|visit|check out|sign up|use code|download)\s*[:\-]?\s*([^\n\r.]+)", full_guideline_content, re.I)
    for cm in cta_matches:
        c_clean = cm.strip()
        if len(c_clean) >= 4 and not any(op.search(c_clean) for op in OPERATIONAL_PATTERNS):
            cta_instructions.append(c_clean)
            if not cta_wording:
                cta_wording = c_clean

    # Build structured brief with all extracted parameters
    brief = WhopCampaignBrief(
        campaign_id=campaign_id,
        title=title,
        campaign_url=campaign_url,
        source_platform="whop",
        payout_raw=payout_raw,
        cpm=cpm,
        supported_platforms=supported_platforms,
        guideline_source_type=source_type,
        guideline_source_reference=source_ref,
        guideline_hash=g_hash,
        parsed_at=datetime.now(timezone.utc).isoformat(),
        raw_guideline_text=full_guideline_content,
        parser_version=PARSER_VERSION,
        parsing_status=ParsingStatus.PARSED,
        duration_min_s=min_dur,
        duration_max_s=max_dur,
        duration_preferred_s=pref_dur,
        duration_exact_s=exact_dur,
        required_topics=[],
        allowed_sources=source_urls,
        forbidden_sources=[],
        banned_words=banned_words,
        banned_topics=banned_topics,
        branding_rules=branding_rules,
        logo_watermark_required=logo_watermark_required,
        hook_instructions=hook_instructions,
        bgm_rules=bgm_rules,
        sfx_rules=sfx_rules,
        youtube_requirements=youtube_reqs,
        instagram_requirements=instagram_reqs,
        tiktok_requirements=tiktok_reqs,
        hashtags=extracted_hashtags,
        required_mentions=extracted_mentions,
        cta_wording=cta_wording,
        cta_instructions=cta_instructions,
        link_in_bio=link_in_bio,
        approval_gate_required=approval_gate_required,
        operational_instructions=operational_instructions,
        rules=rules,
    )

    # Validate brief
    val_errors = validate_campaign_brief(brief)
    if val_errors:
        log.warning("Validation warnings for campaign brief '%s': %s", campaign_id, val_errors)
        if any("Contradictory" in e for e in val_errors):
            brief.parsing_status = ParsingStatus.PARTIAL

    return brief
