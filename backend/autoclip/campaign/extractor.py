"""Campaign Guideline Document Extractor for AL AMR.

Extracts text from PDF and DOCX campaign guideline files and parses them
into a structured CampaignBrief for autonomous highlight detection and scoring.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any, BinaryIO

import pypdf
import docx

from .models import CampaignBrief, generate_campaign_id

log = logging.getLogger(__name__)

SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/octet-stream",
}

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}

ENGLISH_STOPWORDS: set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can't", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought",
    "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves",
    # Guideline framing & syntax terms
    "following", "words", "word", "terms", "term", "topics", "topic", "phrases",
    "phrase", "language", "content", "say", "saying", "mention", "mentioning",
    "use", "using", "etc", "avoid", "banned", "prohibited", "exclude",
}


class GuidelineExtractionError(ValueError):
    """Raised when a guideline document cannot be processed or contains no usable text."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint

    def __str__(self) -> str:
        return f"{super().__str__()}\n\n{self.hint}" if self.hint else super().__str__()


MAX_GUIDELINE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_GUIDELINE_SIZE_BYTES = MAX_GUIDELINE_SIZE


def compute_document_hash(content_bytes: bytes) -> str:
    """Compute SHA-256 hex digest for forensic document identification."""
    import hashlib

    return hashlib.sha256(content_bytes).hexdigest()


def validate_guideline_file(filename: str, content_bytes: bytes, mime_type: str | None = None) -> str:
    """Validate file type and non-empty content. Returns normalized extension (.pdf or .docx)."""
    if not content_bytes or len(content_bytes.strip()) == 0:
        raise GuidelineExtractionError(
            f"The uploaded guideline file '{filename}' is completely empty (0 bytes).",
            hint="Please upload a valid PDF or DOCX campaign guideline document.",
        )

    if len(content_bytes) > MAX_GUIDELINE_SIZE:
        raise GuidelineExtractionError(
            f"The guideline file '{filename}' ({len(content_bytes)} bytes) exceeds the maximum allowed size of 50MB.",
            hint="Please provide a document smaller than 50MB.",
        )

    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise GuidelineExtractionError(
            f"Unsupported file format '{ext}' for guideline document '{filename}'.",
            hint="Only PDF (.pdf) and Microsoft Word (.docx) documents are supported.",
        )

    # Validate header magic bytes
    if ext == ".pdf":
        if not content_bytes.startswith(b"%PDF-"):
            raise GuidelineExtractionError(
                f"The file '{filename}' has a .pdf extension but does not appear to be a valid PDF document.",
                hint="Verify that the file is not corrupted or renamed from another format.",
            )
    elif ext == ".docx":
        # DOCX files are zip containers starting with PK\x03\x04
        if not content_bytes.startswith(b"PK\x03\x04"):
            raise GuidelineExtractionError(
                f"The file '{filename}' has a .docx extension but is not a valid Word document container.",
                hint="Save the file as a modern Word (.docx) document and try again.",
            )

    return ext


def extract_text_from_pdf(stream_or_path: str | Path | BinaryIO | bytes) -> str:
    """Extract ordered text from a PDF file preserving paragraphs and pages."""
    try:
        if isinstance(stream_or_path, bytes):
            fp: BinaryIO = io.BytesIO(stream_or_path)
        elif isinstance(stream_or_path, (str, Path)):
            fp = open(stream_or_path, "rb")
        else:
            fp = stream_or_path

        reader = pypdf.PdfReader(fp)
        pages_text: list[str] = []
        for idx, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            cleaned = page_text.strip()
            if cleaned:
                pages_text.append(cleaned)

        if isinstance(stream_or_path, (str, Path)):
            fp.close()

        full_text = "\n\n".join(pages_text).strip()
        if not full_text:
            raise GuidelineExtractionError(
                "The PDF document contains no readable text.",
                hint="If this document is a scanned image, ensure it has been processed with an OCR layer so text can be extracted.",
            )
        return full_text
    except GuidelineExtractionError:
        raise
    except Exception as exc:
        raise GuidelineExtractionError(
            f"Failed to read PDF document: {exc}",
            hint="The PDF file may be corrupted or password-protected.",
        ) from exc


def extract_text_from_docx(stream_or_path: str | Path | BinaryIO | bytes) -> str:
    """Extract ordered text from a DOCX file including headings, paragraphs, and tables."""
    try:
        if isinstance(stream_or_path, bytes):
            fp: BinaryIO = io.BytesIO(stream_or_path)
        elif isinstance(stream_or_path, (str, Path)):
            fp = open(stream_or_path, "rb")
        else:
            fp = stream_or_path

        doc = docx.Document(fp)
        parts: list[str] = []

        for p in doc.paragraphs:
            text = p.text.strip()
            if text:
                parts.append(text)

        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))

        if isinstance(stream_or_path, (str, Path)):
            fp.close()

        full_text = "\n".join(parts).strip()
        if not full_text:
            raise GuidelineExtractionError(
                "The Word (.docx) document is empty and contains no text.",
                hint="Please provide a campaign guideline document containing your content requirements.",
            )
        return full_text
    except GuidelineExtractionError:
        raise
    except Exception as exc:
        raise GuidelineExtractionError(
            f"Failed to read DOCX document: {exc}",
            hint="Ensure the file is a valid .docx format.",
        ) from exc


def extract_guideline_text(filename: str, content_bytes: bytes) -> tuple[str, str]:
    """Validate and extract raw text from guideline bytes. Returns (text, normalized_ext)."""
    ext = validate_guideline_file(filename, content_bytes)
    if ext == ".pdf":
        text = extract_text_from_pdf(content_bytes)
    elif ext == ".docx":
        text = extract_text_from_docx(content_bytes)
    else:
        raise GuidelineExtractionError(f"Unsupported format: {ext}")
    return text, ext


def parse_guidelines_into_brief(raw_text: str, filename: str = "Guideline") -> CampaignBrief:
    """Parse raw extracted guideline text into a structured CampaignBrief.

    Uses deterministic heuristic extraction to identify topics, duration,
    hooks, CTAs, tone, audience, and banned terms without requiring an active LLM key.
    """
    stem = Path(filename).stem
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    # Infer campaign name
    name = stem.replace("_", " ").replace("-", " ").title()
    if lines:
        first_line = lines[0]
        if len(first_line) < 60 and not any(kw in first_line.lower() for kw in ("page ", "confidential", "date")):
            name = first_line.strip("# ")

    # Section extractors
    lower_text = raw_text.lower()

    # 1. Target Audience
    target_audience = ""
    aud_match = re.search(r"(?:target\s+audience|audience|target\s+demographic|demographic)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE)
    if aud_match:
        target_audience = aud_match.group(1).strip()
    elif "developers" in lower_text:
        target_audience = "Software Developers & Engineers"
    elif "entrepreneurs" in lower_text or "business" in lower_text:
        target_audience = "Entrepreneurs & Business Owners"
    elif "creators" in lower_text:
        target_audience = "Content Creators & Marketers"

    # 2. Tone
    tone = "engaging"
    for candidate_tone in ("educational", "urgent", "inspirational", "entertaining", "authoritative", "casual", "humorous", "storytelling"):
        if candidate_tone in lower_text:
            tone = candidate_tone
            break

    # 3. Duration constraints
    min_dur = 20.0
    max_dur = 75.0
    pref_dur: float | None = 45.0

    # Look for duration patterns like "30-60s", "under 60 seconds", "45s", "between 25 and 55 seconds"
    dur_range = re.search(r"(\d+)\s*(?:-|to|and)\s*(\d+)\s*(?:s|sec|seconds)", lower_text)
    if dur_range:
        d1, d2 = float(dur_range.group(1)), float(dur_range.group(2))
        min_dur = max(10.0, min(d1, d2))
        max_dur = min(120.0, max(d1, d2))
        pref_dur = (min_dur + max_dur) / 2.0
    else:
        under_match = re.search(r"(?:under|less than|max(?:imum)?)\s*(\d+)\s*(?:s|sec|seconds)", lower_text)
        if under_match:
            max_dur = min(120.0, float(under_match.group(1)))
            min_dur = max(10.0, min(20.0, max_dur * 0.4))
            pref_dur = min_dur + (max_dur - min_dur) * 0.6

    # 4. Required Topics & Concepts
    required_topics: list[str] = []
    required_concepts: list[str] = []
    optional_keywords: list[str] = []

    # Find lists or bullet points
    bullet_items = re.findall(r"(?:^|\n)\s*[-*•\d+.]\s+([^\n\r]+)", raw_text)
    for item in bullet_items:
        clean = item.strip()
        if 4 <= len(clean) <= 60 and not clean.lower().startswith(("page", "http", "www")):
            if len(required_topics) < 5:
                required_topics.append(clean)
            elif len(optional_keywords) < 10:
                optional_keywords.append(clean)

    # Keyword search under headings like "Topics:", "Keywords:", "Themes:", "Must feature topic:"
    kw_match = re.search(r"(?:topics|topic|keywords|themes|must\s+include|must\s+feature(?:\s+topic)?|focus\s+areas)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE)
    if kw_match:
        extracted = [w.strip() for w in re.split(r"[,;•|]+", kw_match.group(1)) if w.strip()]
        for kw in extracted:
            if kw and kw not in required_topics:
                required_topics.append(kw)

    # 5. Banned Words / Prohibited Topics
    banned_words: list[str] = []
    # Search for banned words using explicit horizontal whitespace so newlines are not crossed
    banned_matches = re.finditer(
        r"(?:banned|avoid|prohibited|do\s+not\s+mention|exclude)[ \t]*[:\-]?[ \t]*([^\n\r]+)",
        raw_text,
        re.IGNORECASE,
    )
    for b_m in banned_matches:
        raw_captured = b_m.group(1).strip()
        # Strip syntactic preamble like "the following words:" or "words:" or "terms:"
        cleaned_lead = re.sub(
            r"^(?:the\s+)?(?:following\s+)?(?:words|topics|phrases|terms|content)?\s*[:\-]\s*",
            "",
            raw_captured,
            flags=re.IGNORECASE,
        ).strip()
        raw_items = re.split(r"[,;•|\t]+", cleaned_lead)
        for w in raw_items:
            # Strip parenthetical clarifications (e.g. "eligible (bachelor)" -> "eligible")
            base_term = re.sub(r"\(.*?\)", "", w).strip().lower()
            base_term = re.sub(r"^[^\w]+|[^\w]+$", "", base_term)
            if base_term and len(base_term) > 2 and base_term not in ENGLISH_STOPWORDS:
                if base_term not in banned_words:
                    banned_words.append(base_term)

    # Also check if there is a bulleted list immediately following a "banned/avoid" header
    header_match = re.search(
        r"(?:banned|avoid|prohibited|do\s+not\s+say|do\s+not\s+mention)(?:\s+(?:the\s+)?(?:following\s+)?(?:words|topics|terms))?\s*:\s*\n((?:\s*[-*•\d+.]\s+[^\n\r]+\n?)+)",
        raw_text,
        re.IGNORECASE,
    )
    if header_match:
        bullet_lines = re.findall(r"[-*•\d+.]\s+([^\n\r]+)", header_match.group(1))
        for line in bullet_lines:
            base_term = re.sub(r"\(.*?\)", "", line).strip().lower()
            base_term = re.sub(r"^[^\w]+|[^\w]+$", "", base_term)
            if base_term and len(base_term) > 2 and base_term not in ENGLISH_STOPWORDS:
                if base_term not in banned_words:
                    banned_words.append(base_term)

    # 6. Call to Action (CTA)
    cta_required = False
    cta_types: list[str] = []
    cta_instructions: list[str] = []
    cta_text = ""

    cta_heading_match = re.search(r"(?:call\s+to\s+action|cta(?:\s+text|\s+wording|\s+instruction)?|closing\s+cta)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE)
    if cta_heading_match:
        cta_required = True
        cta_text = cta_heading_match.group(1).strip()
        cta_instructions.append(cta_text)

    cta_patterns = (
        (r"\bcall\s+to\s+action\b", "general"),
        (r"\bclosing\s+cta\b", "general"),
        (r"\bcta\b", "general"),
        (r"\bsubscribe\b", "subscribe"),
        (r"\blink\s+in\s+bio\b", "link_in_bio"),
        (r"\bfollow\s+(?:us|for\s+more|me|page|account|channel)\b", "follow"),
        (r"\bcomment\s+below\b", "comment"),
        (r"\bshare\s+this\b", "share"),
    )
    for pat, cta_type in cta_patterns:
        if re.search(pat, lower_text):
            cta_required = True
            if cta_type not in cta_types:
                cta_types.append(cta_type)

    # 7. Aspect ratio
    aspect_ratio: Any = "9:16"
    if "16:9" in lower_text or "landscape" in lower_text:
        aspect_ratio = "16:9"
    elif "1:1" in lower_text or "square" in lower_text:
        aspect_ratio = "1:1"

    # 8. Caption preset preference
    caption_preset = "bold_pop"
    for preset_key in (
        "karaoke_fill", "boxed", "clean_lower", "bold_pop", "classic_professional",
        "neon_glow", "minimal_luxury", "cyber_glitch", "editorial_serif", "fire_punch",
        "sunset_warmth", "ocean_breeze", "monochrome_chic", "retro_arcade", "podcast_subtle",
        "headline_impact", "midnight_blue", "pastel_dream", "crimson_shadow", "emerald_elite",
        "golden_hour", "comic_action", "tech_clean", "slate_modern", "rich_dynamic",
    ):
        if preset_key in lower_text or preset_key.replace("_", " ") in lower_text:
            caption_preset = preset_key
            break
    if caption_preset == "bold_pop":
        if "karaoke" in lower_text:
            caption_preset = "karaoke_fill"
        elif "boxed" in lower_text:
            caption_preset = "boxed"
        elif "clean" in lower_text or "lower" in lower_text:
            caption_preset = "clean_lower"
        elif "classic" in lower_text or "professional" in lower_text:
            caption_preset = "classic_professional"

    # 9. Brand & Key concepts
    branding_rules: list[str] = []
    brand_match = re.search(r"(?:brand|product|company)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE)
    if brand_match:
        brand_terms = [b.strip() for b in re.split(r"[,;•|]+", brand_match.group(1)) if b.strip()]
        for b in brand_terms:
            if b and b not in required_concepts:
                required_concepts.append(b)
                branding_rules.append(b)

    # 10. Hashtags extraction
    hashtags: list[str] = []
    raw_hash_matches = re.findall(r"#[A-Za-z0-9_]+", raw_text)
    for h in raw_hash_matches:
        if h not in hashtags:
            hashtags.append(h)
    tag_heading_match = re.search(r"(?:hashtags?|tags?)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE)
    if tag_heading_match:
        extra_tags = [t.strip() for t in re.split(r"[,;\s]+", tag_heading_match.group(1)) if t.strip()]
        for t in extra_tags:
            norm_tag = t if t.startswith("#") else f"#{t}"
            if norm_tag not in hashtags:
                hashtags.append(norm_tag)

    # 11. Title Patterns extraction
    title_patterns: list[str] = []
    for tm in re.finditer(r"(?:title(?:\s+pattern|\s+format|\s+requirements)?|headline)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE):
        t_val = tm.group(1).strip()
        if t_val and t_val not in title_patterns:
            title_patterns.append(t_val)

    # 12. Description Guidelines & links extraction
    description_guidelines: list[str] = []
    for dm in re.finditer(r"(?:description|caption(?:\s+requirements)?|body\s+copy|link\s+in\s+description)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE):
        d_val = dm.group(1).strip()
        if d_val and d_val not in description_guidelines:
            description_guidelines.append(d_val)
    raw_urls = re.findall(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+", raw_text)
    for u in raw_urls:
        url_note = f"Include link: {u}"
        if url_note not in description_guidelines:
            description_guidelines.append(url_note)

    # 13. Required Mentions extraction
    required_mentions: list[str] = []
    raw_mention_matches = re.findall(r"@[A-Za-z0-9_.]+", raw_text)
    for m in raw_mention_matches:
        if m not in required_mentions:
            required_mentions.append(m)
    mention_heading_match = re.search(r"(?:mention|handle|tag\s+account)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE)
    if mention_heading_match:
        extra_mentions = [m.strip() for m in re.split(r"[,;\s]+", mention_heading_match.group(1)) if m.strip()]
        for m in extra_mentions:
            norm_m = m if m.startswith("@") else f"@{m}"
            if norm_m not in required_mentions:
                required_mentions.append(norm_m)

    # 14. Mandatory vs Preference Rules classification
    mandatory_rules: list[str] = []
    preference_rules: list[str] = []
    mandatory_pattern = re.compile(r"\b(must|mandatory|required|strictly|shall|do not|never|prohibited)\b", re.IGNORECASE)
    preference_pattern = re.compile(r"\b(prefer|preferred|optional|recommended|nice to have|ideally)\b", re.IGNORECASE)

    for line in lines:
        if len(line) < 15 or len(line) > 200:
            continue
        if mandatory_pattern.search(line):
            if line not in mandatory_rules:
                mandatory_rules.append(line)
        elif preference_pattern.search(line):
            if line not in preference_rules:
                preference_rules.append(line)

    # Context summary (first 500 chars)
    topic_context = raw_text[:500].strip()

    return CampaignBrief(
        campaign_id=generate_campaign_id(),
        name=name[:80],
        description=f"Extracted from guideline document: {filename}",
        topic_context=topic_context,
        target_audience=target_audience,
        required_topics=required_topics[:8],
        required_concepts=required_concepts[:8],
        optional_keywords=optional_keywords[:15],
        banned_words=banned_words[:15],
        minimum_duration=min_dur,
        maximum_duration=max_dur,
        preferred_duration=pref_dur,
        hook_required=True,
        minimum_hook_score=6.0,
        minimum_viral_score=5.0,
        cta_required=cta_required,
        cta_types=cta_types,
        tone=tone,
        aspect_ratio=aspect_ratio,
        caption_preset=caption_preset,
        output_count=5,
        hashtags=hashtags,
        title_patterns=title_patterns,
        description_guidelines=description_guidelines,
        required_mentions=required_mentions,
        cta_instructions=cta_instructions,
        cta_text=cta_text,
        branding_rules=branding_rules,
        mandatory_rules=mandatory_rules,
        preference_rules=preference_rules,
    )


# Canonical alias
extract_campaign_from_text = parse_guidelines_into_brief
