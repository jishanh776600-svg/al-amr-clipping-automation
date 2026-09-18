"""Multi-Document Campaign Normalization & Intelligence Engine for AL AMR.

Aggregates multiple PDF, DOCX, Google Drive, Google Docs, and Campaign URL
materials into a unified, normalized CampaignSpecification. Preserves provenance,
differentiates explicit vs inferred requirements, and detects contradictions.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, BinaryIO

from .conflict_detector import (
    check_aspect_ratio_conflict,
    check_banned_vs_required_conflict,
    check_cta_conflict,
    check_duration_conflict,
)
from .drive_retriever import retrieve_drive_guideline
from .extractor import (
    GuidelineExtractionError,
    compute_document_hash,
    extract_guideline_text,
    parse_guidelines_into_brief,
)
from .models_intelligence import (
    CampaignConflict,
    CampaignSpecification,
    IngestedDocument,
    RequirementItem,
    utcnow_str,
)
from .url_extractor import ExtractedUrlContent, extract_campaign_url

log = logging.getLogger(__name__)

EXPLICIT_TRIGGERS = re.compile(
    r"\b(must|mandatory|required|strictly|shall|do not|never|prohibited|forbidden|cannot|under no circumstances)\b",
    re.IGNORECASE,
)


def _determine_confidence(text: str, snippet: str = "") -> str:
    """Classify requirement as explicit if stated with mandatory language, else inferred."""
    check_target = snippet or text
    if EXPLICIT_TRIGGERS.search(check_target):
        return "explicit"
    return "inferred"


def _extract_snippet(text: str, keyword: str, max_chars: int = 150) -> str:
    """Extract a small snippet of text around a matching keyword for provenance."""
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return text[:max_chars].strip()
    start = max(0, idx - 40)
    end = min(len(text), idx + len(keyword) + 60)
    snippet = text[start:end].replace("\n", " ").strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(text):
        snippet = f"{snippet}..."
    return snippet


class CampaignNormalizer:
    """Normalizes multiple campaign documents and URLs into one CampaignSpecification."""

    def __init__(self, campaign_id: str | None = None) -> None:
        self.campaign_id = campaign_id

    def ingest_files(
        self,
        files: list[tuple[str, bytes | BinaryIO | str | Path]],
        source_type: str | None = None,
    ) -> list[IngestedDocument]:
        """Ingests and extracts text from a list of uploaded PDF / DOCX files."""
        docs: list[IngestedDocument] = []

        for filename, stream_or_path in files:
            doc = IngestedDocument(
                filename=filename,
                source_type=source_type or ("pdf" if filename.lower().endswith(".pdf") else "docx"),
            )
            try:
                # Read bytes
                if isinstance(stream_or_path, (str, Path)):
                    content = Path(stream_or_path).read_bytes()
                elif isinstance(stream_or_path, bytes):
                    content = stream_or_path
                else:
                    content = stream_or_path.read()

                doc.size_bytes = len(content)
                doc.sha256 = compute_document_hash(content)

                raw_text, ext = extract_guideline_text(filename, content)
                doc.raw_text = raw_text
                doc.word_count = len(re.findall(r"\b\w+\b", raw_text))
                doc.char_count = len(raw_text)
                doc.status = "extracted"
                if not source_type:
                    doc.source_type = "pdf" if ext == ".pdf" else "docx"

            except GuidelineExtractionError as exc:
                log.warning("Document extraction failed for '%s': %s", filename, exc)
                doc.status = "failed"
                doc.error = str(exc)
            except Exception as exc:
                log.error("Unexpected error reading document '%s': %s", filename, exc)
                doc.status = "failed"
                doc.error = f"Failed to extract document: {exc}"

            docs.append(doc)

        return docs

    def ingest_drive_urls(self, drive_urls: list[str]) -> list[IngestedDocument]:
        """Ingests and extracts text from Google Drive or Google Docs URLs."""
        docs: list[IngestedDocument] = []

        for url in drive_urls:
            clean_url = url.strip()
            if not clean_url:
                continue

            doc = IngestedDocument(
                filename=clean_url,
                source_url=clean_url,
                source_type="google_drive",
            )

            try:
                filename, content, mime_type, file_id = retrieve_drive_guideline(clean_url)
                doc.filename = filename
                doc.size_bytes = len(content)
                doc.sha256 = compute_document_hash(content)
                doc.source_type = "google_docs" if "google_doc" in filename else "google_drive"

                raw_text, _ = extract_guideline_text(filename, content)
                doc.raw_text = raw_text
                doc.word_count = len(re.findall(r"\b\w+\b", raw_text))
                doc.char_count = len(raw_text)
                doc.status = "extracted"

            except GuidelineExtractionError as exc:
                log.warning("Drive extraction failed for '%s': %s", clean_url, exc)
                doc.status = "failed"
                doc.error = str(exc)
            except Exception as exc:
                log.error("Unexpected error retrieving Drive document '%s': %s", clean_url, exc)
                doc.status = "failed"
                doc.error = f"Drive retrieval failed: {exc}"

            docs.append(doc)

        return docs

    def ingest_campaign_url(self, url: str) -> tuple[IngestedDocument | None, ExtractedUrlContent | None]:
        """Ingests a campaign web landing page / URL."""
        if not url or not url.strip():
            return None, None

        clean_url = url.strip()
        extracted = extract_campaign_url(clean_url)

        doc = IngestedDocument(
            filename=extracted.title or clean_url,
            source_url=clean_url,
            source_type="campaign_url",
            size_bytes=len(extracted.raw_text.encode("utf-8")),
            sha256=compute_document_hash(extracted.raw_text.encode("utf-8")) if extracted.raw_text else None,
            word_count=extracted.word_count,
            char_count=extracted.char_count,
            raw_text=extracted.raw_text,
            status="extracted" if extracted.status == "extracted" else "failed",
            error=extracted.error,
        )

        return doc, extracted

    def normalize(
        self,
        documents: list[IngestedDocument],
        campaign_url: str | None = None,
    ) -> CampaignSpecification:
        """Merges all valid documents into one normalized CampaignSpecification."""
        spec = CampaignSpecification(
            campaign_id=self.campaign_id or CampaignSpecification().campaign_id,
            campaign_url=campaign_url,
        )

        # 1. Store all documents (including failed ones for provenance)
        spec.documents = list(documents)

        # 2. Check for document extraction failures and record warnings
        valid_docs: list[IngestedDocument] = []
        for doc in documents:
            if doc.status == "failed":
                spec.warnings.append(
                    f"Campaign document '{doc.filename}' failed to ingest: {doc.error}. Remaining materials will be used."
                )
            else:
                valid_docs.append(doc)

        if not valid_docs:
            if documents:
                spec.warnings.append("All provided campaign documents failed extraction. Using default clipping parameters.")
            return spec

        # 3. Process individual briefs from each document
        per_doc_briefs: list[tuple[IngestedDocument, Any]] = []
        for doc in valid_docs:
            try:
                brief = parse_guidelines_into_brief(doc.raw_text, doc.filename)
                per_doc_briefs.append((doc, brief))
            except Exception as exc:
                log.warning("Failed to parse brief from '%s': %s", doc.filename, exc)
                spec.warnings.append(f"Could not parse rules from '{doc.filename}': {exc}")

        if not per_doc_briefs:
            return spec

        # 4. Infer Campaign Title & Context
        titles = [b.name for _, b in per_doc_briefs if b.name and "Default Campaign" not in b.name]
        spec.title = titles[0] if titles else per_doc_briefs[0][1].name
        spec.description = "Combined campaign specification from: " + ", ".join(d.filename for d in valid_docs)

        # Accumulate context and audience
        contexts = [d.raw_text[:300].strip() for d in valid_docs if d.raw_text]
        spec.objective = "\n---\n".join(contexts[:3])
        spec.target_audience = next((b.target_audience for _, b in per_doc_briefs if b.target_audience), "")

        # 5. Conflict Detection across Document Pairs
        n = len(per_doc_briefs)
        for i in range(n):
            doc_a, brief_a = per_doc_briefs[i]
            for j in range(i + 1, n):
                doc_b, brief_b = per_doc_briefs[j]

                # 5a. Duration Conflict Check
                dur_conf = check_duration_conflict(
                    doc_a, (brief_a.minimum_duration, brief_a.maximum_duration),
                    doc_b, (brief_b.minimum_duration, brief_b.maximum_duration),
                )
                if dur_conf:
                    spec.conflicts.append(dur_conf)

                # 5b. Aspect Ratio Conflict Check
                ratio_conf = check_aspect_ratio_conflict(
                    doc_a, brief_a.aspect_ratio,
                    doc_b, brief_b.aspect_ratio,
                )
                if ratio_conf:
                    spec.conflicts.append(ratio_conf)

                # 5c. Required vs Banned Topic Conflicts
                spec.conflicts.extend(
                    check_banned_vs_required_conflict(doc_a, brief_a.required_topics, doc_b, brief_b.banned_words)
                )
                spec.conflicts.extend(
                    check_banned_vs_required_conflict(doc_b, brief_b.required_topics, doc_a, brief_a.banned_words)
                )

                # 5d. CTA Conflict Check
                cta_conf = check_cta_conflict(
                    doc_a, brief_a.cta_required, doc_b, brief_b.cta_required, doc_b.raw_text
                )
                if cta_conf:
                    spec.conflicts.append(cta_conf)

        # 6. Aggregate Compatible Rules
        # Topics & Themes (preserve source provenance)
        seen_topics: set[str] = set()
        for doc, brief in per_doc_briefs:
            for t in brief.required_topics:
                norm_t = t.lower().strip()
                if norm_t and norm_t not in seen_topics:
                    seen_topics.add(norm_t)
                    snippet = _extract_snippet(doc.raw_text, t)
                    confidence = _determine_confidence(doc.raw_text, snippet)
                    spec.desired_topics.append(RequirementItem(
                        value=t,
                        confidence=confidence,  # type: ignore
                        source_doc_id=doc.doc_id,
                        source_filename=doc.filename,
                        source_type=doc.source_type,
                        snippet=snippet,
                    ))

        # Concepts / Themes
        seen_concepts: set[str] = set()
        for doc, brief in per_doc_briefs:
            for c in brief.required_concepts:
                norm_c = c.lower().strip()
                if norm_c and norm_c not in seen_concepts:
                    seen_concepts.add(norm_c)
                    snippet = _extract_snippet(doc.raw_text, c)
                    confidence = _determine_confidence(doc.raw_text, snippet)
                    spec.required_themes.append(RequirementItem(
                        value=c,
                        confidence=confidence,  # type: ignore
                        source_doc_id=doc.doc_id,
                        source_filename=doc.filename,
                        source_type=doc.source_type,
                        snippet=snippet,
                    ))

        # Banned terms & words
        seen_banned: set[str] = set()
        for doc, brief in per_doc_briefs:
            for b in (brief.banned_words + getattr(brief, "banned_topics", [])):
                norm_b = b.lower().strip()
                if norm_b and norm_b not in seen_banned:
                    seen_banned.add(norm_b)
                    snippet = _extract_snippet(doc.raw_text, b)
                    req_item = RequirementItem(
                        value=b,
                        confidence="explicit",  # Banned terms are explicit rules
                        source_doc_id=doc.doc_id,
                        source_filename=doc.filename,
                        source_type=doc.source_type,
                        snippet=snippet,
                    )
                    spec.banned_words.append(req_item)
                    spec.banned_topics.append(req_item)

        # Keywords
        seen_kw: set[str] = set()
        for doc, brief in per_doc_briefs:
            for kw in brief.optional_keywords:
                norm_kw = kw.lower().strip()
                if norm_kw and norm_kw not in seen_kw:
                    seen_kw.add(norm_kw)
                    spec.keywords.append(RequirementItem(
                        value=kw,
                        confidence="inferred",
                        source_doc_id=doc.doc_id,
                        source_filename=doc.filename,
                        source_type=doc.source_type,
                        snippet=_extract_snippet(doc.raw_text, kw),
                    ))

        # Clip Duration Constraints: Merge overlapping bounds if no critical conflict
        has_dur_crit = any(c.rule_category == "duration" and c.severity == "critical" and c.resolution_status == "unresolved" for c in spec.conflicts)
        if not has_dur_crit:
            # Compatible: compute bounding envelope
            min_durs = [b.minimum_duration for _, b in per_doc_briefs]
            max_durs = [b.maximum_duration for _, b in per_doc_briefs]
            merged_min = max(min_durs)
            merged_max = min(max_durs)

            if merged_min <= merged_max:
                doc_src = per_doc_briefs[0][0]
                spec.duration_min_s = RequirementItem(
                    value=merged_min,
                    confidence="explicit" if any(EXPLICIT_TRIGGERS.search(d.raw_text) for d, _ in per_doc_briefs) else "inferred",
                    source_doc_id=doc_src.doc_id,
                    source_filename=doc_src.filename,
                    source_type=doc_src.source_type,
                )
                spec.duration_max_s = RequirementItem(
                    value=merged_max,
                    confidence="explicit" if any(EXPLICIT_TRIGGERS.search(d.raw_text) for d, _ in per_doc_briefs) else "inferred",
                    source_doc_id=doc_src.doc_id,
                    source_filename=doc_src.filename,
                    source_type=doc_src.source_type,
                )
                spec.duration_preferred_s = RequirementItem(
                    value=round((merged_min + merged_max) / 2.0, 1),
                    confidence="inferred",
                    source_doc_id=doc_src.doc_id,
                    source_filename=doc_src.filename,
                    source_type=doc_src.source_type,
                )

        # Aspect Ratio: Merge if consistent
        has_ratio_crit = any(c.rule_category == "aspect_ratio" and c.severity == "critical" and c.resolution_status == "unresolved" for c in spec.conflicts)
        if not has_ratio_crit:
            first_doc, first_brief = per_doc_briefs[0]
            spec.aspect_ratio = RequirementItem(
                value=first_brief.aspect_ratio,
                confidence="explicit" if "9:16" in first_doc.raw_text or "16:9" in first_doc.raw_text else "inferred",
                source_doc_id=first_doc.doc_id,
                source_filename=first_doc.filename,
                source_type=first_doc.source_type,
            )

        # Hook Rules
        hook_docs = [(d, b) for d, b in per_doc_briefs if b.hook_required]
        if hook_docs:
            h_doc, h_brief = hook_docs[0]
            spec.hook_required = RequirementItem(
                value=True,
                confidence="explicit",
                source_doc_id=h_doc.doc_id,
                source_filename=h_doc.filename,
                source_type=h_doc.source_type,
                snippet=_extract_snippet(h_doc.raw_text, "hook"),
            )
            spec.hook_window_s = RequirementItem(
                value=h_brief.hook_window_seconds,
                confidence="inferred",
                source_doc_id=h_doc.doc_id,
                source_filename=h_doc.filename,
                source_type=h_doc.source_type,
            )
            for d, b in per_doc_briefs:
                for ht in b.hook_types:
                    spec.hook_types.append(RequirementItem(
                        value=ht,
                        confidence="inferred",
                        source_doc_id=d.doc_id,
                        source_filename=d.filename,
                    ))

        # CTA Rules
        cta_docs = [(d, b) for d, b in per_doc_briefs if b.cta_required]
        if cta_docs:
            c_doc, c_brief = cta_docs[0]
            spec.cta_required = RequirementItem(
                value=True,
                confidence="explicit",
                source_doc_id=c_doc.doc_id,
                source_filename=c_doc.filename,
                source_type=c_doc.source_type,
                snippet=_extract_snippet(c_doc.raw_text, "call to action"),
            )
            for d, b in per_doc_briefs:
                for ct in b.cta_types:
                    spec.cta_types.append(RequirementItem(
                        value=ct,
                        confidence="inferred",
                        source_doc_id=d.doc_id,
                        source_filename=d.filename,
                    ))

                for ci in getattr(b, "cta_instructions", []):
                    spec.cta_instructions.append(RequirementItem(
                        value=ci,
                        confidence="explicit",
                        source_doc_id=d.doc_id,
                        source_filename=d.filename,
                    ))
                if getattr(b, "cta_text", None):
                    spec.cta_instructions.append(RequirementItem(
                        value=b.cta_text,
                        confidence="explicit",
                        source_doc_id=d.doc_id,
                        source_filename=d.filename,
                    ))

        # SEO & Metadata Requirements
        for d, b in per_doc_briefs:
            for h in getattr(b, "hashtags", []):
                if h not in [x.value for x in spec.hashtags]:
                    spec.hashtags.append(RequirementItem(
                        value=h,
                        confidence="explicit",
                        source_doc_id=d.doc_id,
                        source_filename=d.filename,
                    ))
            for p in getattr(b, "title_patterns", []):
                if p not in [x.value for x in spec.title_patterns]:
                    spec.title_patterns.append(RequirementItem(
                        value=p,
                        confidence="explicit",
                        source_doc_id=d.doc_id,
                        source_filename=d.filename,
                    ))
            for g in getattr(b, "description_guidelines", []):
                if g not in [x.value for x in spec.description_guidelines]:
                    spec.description_guidelines.append(RequirementItem(
                        value=g,
                        confidence="explicit",
                        source_doc_id=d.doc_id,
                        source_filename=d.filename,
                    ))
            for m in getattr(b, "required_mentions", []):
                if m not in [x.value for x in spec.required_mentions]:
                    spec.required_mentions.append(RequirementItem(
                        value=m,
                        confidence="explicit",
                        source_doc_id=d.doc_id,
                        source_filename=d.filename,
                    ))
            for r in getattr(b, "branding_rules", []):
                if r not in [x.value for x in spec.branding_rules]:
                    spec.branding_rules.append(RequirementItem(
                        value=r,
                        confidence="explicit",
                        source_doc_id=d.doc_id,
                        source_filename=d.filename,
                    ))

        # Style, Pacing, Caption Preset
        first_doc, first_brief = per_doc_briefs[0]
        spec.tone = RequirementItem(
            value=first_brief.tone,
            confidence="inferred",
            source_doc_id=first_doc.doc_id,
            source_filename=first_doc.filename,
        )
        spec.pacing = RequirementItem(
            value=first_brief.pacing,
            confidence="inferred",
            source_doc_id=first_doc.doc_id,
            source_filename=first_doc.filename,
        )
        spec.caption_preset = RequirementItem(
            value=first_brief.caption_preset,
            confidence="inferred",
            source_doc_id=first_doc.doc_id,
            source_filename=first_doc.filename,
        )

        return spec
