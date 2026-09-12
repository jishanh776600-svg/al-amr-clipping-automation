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

    # Look for duration patterns like "30-60s", "under 60 seconds", "45s"
    dur_range = re.search(r"(\d+)\s*(?:-|to)\s*(\d+)\s*(?:s|sec|seconds)", lower_text)
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

    # Keyword search under headings like "Topics:", "Keywords:", "Themes:"
    kw_match = re.search(r"(?:topics|keywords|themes|must\s+include|focus\s+areas)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE)
    if kw_match:
        extracted = [w.strip() for w in re.split(r"[,;•|]+", kw_match.group(1)) if w.strip()]
        for kw in extracted:
            if kw and kw not in required_topics:
                required_topics.append(kw)

    # 5. Banned Words / Prohibited Topics
    banned_words: list[str] = []
    banned_match = re.search(r"(?:banned|avoid|prohibited|do\s+not\s+mention|exclude)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE)
    if banned_match:
        banned_words = [w.strip().lower() for w in re.split(r"[,;•|]+", banned_match.group(1)) if w.strip()]

    # 6. Call to Action (CTA)
    cta_required = False
    cta_types: list[str] = []
    if any(phrase in lower_text for phrase in ("call to action", "cta", "subscribe", "link in bio", "follow", "comment below", "share this")):
        cta_required = True
        if "subscribe" in lower_text:
            cta_types.append("subscribe")
        if "link in bio" in lower_text or "link" in lower_text:
            cta_types.append("link_in_bio")
        if "comment" in lower_text:
            cta_types.append("comment")
        if "share" in lower_text:
            cta_types.append("share")
        if not cta_types:
            cta_types = ["general"]

    # 7. Aspect ratio
    aspect_ratio: Any = "9:16"
    if "16:9" in lower_text or "landscape" in lower_text:
        aspect_ratio = "16:9"
    elif "1:1" in lower_text or "square" in lower_text:
        aspect_ratio = "1:1"

    # 8. Caption preset preference
    caption_preset = "bold_pop"
    if "karaoke" in lower_text:
        caption_preset = "karaoke_fill"
    elif "boxed" in lower_text:
        caption_preset = "boxed"
    elif "clean" in lower_text or "lower" in lower_text:
        caption_preset = "clean_lower"

    # 9. Brand & Key concepts
    brand_match = re.search(r"(?:brand|product|company)[:\s-]+([^\n\r]+)", raw_text, re.IGNORECASE)
    if brand_match:
        brand_terms = [b.strip() for b in re.split(r"[,;•|]+", brand_match.group(1)) if b.strip()]
        for b in brand_terms:
            if b and b not in required_concepts:
                required_concepts.append(b)

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
    )
