"""Campaign Requirement Conflict Detector for AL AMR.

Identifies contradictions and incompatible requirements across multiple
campaign guideline documents without silently dropping or guessing rules.
"""

from __future__ import annotations

import re
from typing import Any

from .models_intelligence import CampaignConflict, IngestedDocument


def detect_superseding(doc_a_name: str, doc_b_name: str, text_a: str, text_b: str) -> tuple[bool, str]:
    """Check if one document explicitly supersedes another (e.g. v2 vs v1)."""
    # Check version patterns in filename or text
    v_a = re.search(r"v(?:er(?:sion)?)?\.?\s*(\d+(?:\.\d+)?)", doc_a_name, re.IGNORECASE)
    v_b = re.search(r"v(?:er(?:sion)?)?\.?\s*(\d+(?:\.\d+)?)", doc_b_name, re.IGNORECASE)
    if v_a and v_b:
        try:
            num_a = float(v_a.group(1))
            num_b = float(v_b.group(1))
            if num_b > num_a:
                return True, f"'{doc_b_name}' (v{num_b}) supersedes '{doc_a_name}' (v{num_a})"
            elif num_a > num_b:
                return True, f"'{doc_a_name}' (v{num_a}) supersedes '{doc_b_name}' (v{num_b})"
        except ValueError:
            pass

    # Check text for "supersedes" or "updated"
    if any(phrase in text_b.lower() for phrase in ("supersedes", "updated guidelines", "replaces previous", "new version")):
        return True, f"'{doc_b_name}' explicitly states it updates/supersedes previous guidelines"
    if any(phrase in text_a.lower() for phrase in ("supersedes", "updated guidelines", "replaces previous", "new version")):
        return True, f"'{doc_a_name}' explicitly states it updates/supersedes previous guidelines"

    return False, ""


def check_duration_conflict(
    doc_a: IngestedDocument,
    dur_a: tuple[float, float],
    doc_b: IngestedDocument,
    dur_b: tuple[float, float],
) -> CampaignConflict | None:
    """Check for conflicting clip duration ranges."""
    min_a, max_a = dur_a
    min_b, max_b = dur_b

    # Check overlap
    overlap_min = max(min_a, min_b)
    overlap_max = min(max_a, max_b)

    if overlap_min > overlap_max:
        # Disjoint duration intervals! Hard contradiction.
        is_superseded, reason = detect_superseding(doc_a.filename, doc_b.filename, doc_a.raw_text, doc_b.raw_text)
        return CampaignConflict(
            rule_category="duration",
            severity="critical",
            document_a={
                "doc_id": doc_a.doc_id,
                "filename": doc_a.filename,
                "value": f"{min_a:.0f}s-{max_a:.0f}s",
            },
            document_b={
                "doc_id": doc_b.doc_id,
                "filename": doc_b.filename,
                "value": f"{min_b:.0f}s-{max_b:.0f}s",
            },
            description=(
                f"Contradictory clip duration ranges: '{doc_a.filename}' specifies {min_a:.0f}s-{max_a:.0f}s "
                f"while '{doc_b.filename}' specifies {min_b:.0f}s-{max_b:.0f}s with no overlapping valid duration."
            ),
            resolution_status="superseded" if is_superseded else "unresolved",
            resolution_notes=reason if is_superseded else None,
        )
    return None


def check_aspect_ratio_conflict(
    doc_a: IngestedDocument,
    ratio_a: str,
    doc_b: IngestedDocument,
    ratio_b: str,
) -> CampaignConflict | None:
    """Check for conflicting required video aspect ratios."""
    if ratio_a != ratio_b:
        is_superseded, reason = detect_superseding(doc_a.filename, doc_b.filename, doc_a.raw_text, doc_b.raw_text)
        return CampaignConflict(
            rule_category="aspect_ratio",
            severity="critical",
            document_a={
                "doc_id": doc_a.doc_id,
                "filename": doc_a.filename,
                "value": ratio_a,
            },
            document_b={
                "doc_id": doc_b.doc_id,
                "filename": doc_b.filename,
                "value": ratio_b,
            },
            description=(
                f"Contradictory aspect ratio requirements: '{doc_a.filename}' requires {ratio_a} "
                f"while '{doc_b.filename}' requires {ratio_b}."
            ),
            resolution_status="superseded" if is_superseded else "unresolved",
            resolution_notes=reason if is_superseded else None,
        )
    return None


def check_banned_vs_required_conflict(
    doc_a: IngestedDocument,
    required_topics_a: list[str],
    doc_b: IngestedDocument,
    banned_topics_b: list[str],
) -> list[CampaignConflict]:
    """Check if document A requires a topic that document B explicitly bans."""
    conflicts: list[CampaignConflict] = []
    banned_normalized = {t.lower().strip(): t for t in banned_topics_b if t.strip()}

    for topic in required_topics_a:
        t_norm = topic.lower().strip()
        if not t_norm:
            continue
        # Direct or substring match
        matched_ban = None
        if t_norm in banned_normalized:
            matched_ban = banned_normalized[t_norm]
        else:
            for b_norm, b_orig in banned_normalized.items():
                if len(b_norm) >= 4 and (b_norm in t_norm or t_norm in b_norm):
                    matched_ban = b_orig
                    break

        if matched_ban:
            is_superseded, reason = detect_superseding(doc_a.filename, doc_b.filename, doc_a.raw_text, doc_b.raw_text)
            conflicts.append(CampaignConflict(
                rule_category="topics",
                severity="critical",
                document_a={
                    "doc_id": doc_a.doc_id,
                    "filename": doc_a.filename,
                    "value": f"Required topic: '{topic}'",
                },
                document_b={
                    "doc_id": doc_b.doc_id,
                    "filename": doc_b.filename,
                    "value": f"Banned topic: '{matched_ban}'",
                },
                description=(
                    f"Topic contradiction: '{doc_a.filename}' mandates topic '{topic}', "
                    f"but '{doc_b.filename}' lists '{matched_ban}' as prohibited/banned."
                ),
                resolution_status="superseded" if is_superseded else "unresolved",
                resolution_notes=reason if is_superseded else None,
            ))
    return conflicts


def check_cta_conflict(
    doc_a: IngestedDocument,
    cta_req_a: bool,
    doc_b: IngestedDocument,
    cta_req_b: bool,
    text_b: str,
) -> CampaignConflict | None:
    """Check if one document requires a CTA while another explicitly bans CTAs."""
    if cta_req_a:
        # Check if doc_b explicitly bans CTAs
        if any(p in text_b.lower() for p in ("no call to action", "no cta", "do not include cta", "avoid cta")):
            is_superseded, reason = detect_superseding(doc_a.filename, doc_b.filename, doc_a.raw_text, doc_b.raw_text)
            return CampaignConflict(
                rule_category="cta",
                severity="critical",
                document_a={
                    "doc_id": doc_a.doc_id,
                    "filename": doc_a.filename,
                    "value": "CTA is mandatory",
                },
                document_b={
                    "doc_id": doc_b.doc_id,
                    "filename": doc_b.filename,
                    "value": "CTA is prohibited",
                },
                description=(
                    f"CTA conflict: '{doc_a.filename}' requires a Call to Action, "
                    f"while '{doc_b.filename}' explicitly bans CTAs."
                ),
                resolution_status="superseded" if is_superseded else "unresolved",
                resolution_notes=reason if is_superseded else None,
            )
    return None
