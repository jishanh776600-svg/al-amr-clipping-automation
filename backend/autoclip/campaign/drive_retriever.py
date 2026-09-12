"""Google Drive Campaign Guideline Document Retriever for AL AMR.

Supports retrieving campaign guidelines directly from Google Drive share links,
Google Docs URLs, or raw Drive file IDs. Uses DriveStorageVault when configured,
with automated export of native Google Docs to PDF and HTTP fallback for public links.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from .extractor import GuidelineExtractionError

log = logging.getLogger(__name__)

MAX_GUIDELINE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

DRIVE_ID_PATTERNS = [
    re.compile(r"/file/d/([a-zA-Z0-9_-]{15,})"),
    re.compile(r"/document/d/([a-zA-Z0-9_-]{15,})"),
    re.compile(r"[?&]id=([a-zA-Z0-9_-]{15,})"),
    re.compile(r"^([a-zA-Z0-9_-]{15,})$"),
]


def extract_drive_id(file_id_or_url: str) -> str | None:
    """Extracts a normalized Google Drive file ID from a URL or raw ID string."""
    if not file_id_or_url:
        return None
    raw = file_id_or_url.strip()
    for pattern in DRIVE_ID_PATTERNS:
        match = pattern.search(raw)
        if match:
            return match.group(1)
    return None


def retrieve_drive_guideline(file_id_or_url: str) -> tuple[str, bytes, str, str]:
    """Retrieves a campaign guideline document from Google Drive.

    Returns:
        tuple of (filename, file_bytes, mime_type, drive_file_id)

    Raises:
        GuidelineExtractionError: If the document cannot be retrieved, parsed, or exceeds size limits.
    """
    file_id = extract_drive_id(file_id_or_url)
    if not file_id:
        raise GuidelineExtractionError(
            f"Could not parse a valid Google Drive file ID from '{file_id_or_url}'.",
            hint="Please provide a valid Google Drive share link (e.g. https://drive.google.com/file/d/...) or Google Docs link (https://docs.google.com/document/d/...).",
        )

    # Strategy 1: Attempt retrieval via authenticated DriveStorageVault if credentials exist
    try:
        from ..storage.drive import DriveStorageVault

        vault = DriveStorageVault()
        if vault.is_configured:
            service = vault._get_service()
            meta = service.files().get(fileId=file_id, fields="id, name, mimeType, size").execute()
            name = meta.get("name", f"drive_doc_{file_id}")
            mime = meta.get("mimeType", "")

            # If it's a native Google Doc, export directly to PDF
            if mime == "application/vnd.google-apps.document":
                content = service.files().export_media(fileId=file_id, mimeType="application/pdf").execute()
                stem = Path(name).stem or f"google_doc_{file_id}"
                filename = f"{stem}.pdf"
                mime_type = "application/pdf"
            else:
                content = service.files().get_media(fileId=file_id).execute()
                filename = name
                mime_type = mime or "application/octet-stream"

            if content and len(content) > 0:
                if len(content) > MAX_GUIDELINE_SIZE_BYTES:
                    raise GuidelineExtractionError(
                        f"Google Drive document '{filename}' exceeds maximum allowed size of 50MB.",
                        hint="Please supply a smaller PDF or Word document.",
                    )
                return filename, content, mime_type, file_id
    except GuidelineExtractionError:
        raise
    except Exception as exc:
        log.info("DriveStorageVault retrieval for %s failed or not configured (%s); attempting HTTP fallback", file_id, exc)

    # Strategy 2: Direct export / download via Google Drive HTTP endpoints
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AL-AMR-Automation/1.0)",
    }

    # 2a. Try Google Docs PDF export
    doc_export_url = f"https://docs.google.com/document/d/{file_id}/export?format=pdf"
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0, headers=headers) as client:
            resp = client.get(doc_export_url)
            if resp.status_code == 200 and resp.content.startswith(b"%PDF-"):
                content = resp.content
                if len(content) > MAX_GUIDELINE_SIZE_BYTES:
                    raise GuidelineExtractionError(
                        f"Google Drive document '{file_id}' exceeds maximum allowed size of 50MB.",
                        hint="Please supply a smaller PDF or Word document.",
                    )
                return f"google_doc_{file_id}.pdf", content, "application/pdf", file_id
    except GuidelineExtractionError:
        raise
    except Exception as exc:
        log.debug("HTTP Google Docs export failed: %s", exc)

    # 2b. Try Google Drive direct file download
    drive_dl_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0, headers=headers) as client:
            resp = client.get(drive_dl_url)
            if resp.status_code == 200:
                content = resp.content
                if content.startswith(b"%PDF-"):
                    if len(content) > MAX_GUIDELINE_SIZE_BYTES:
                        raise GuidelineExtractionError("Google Drive document exceeds 50MB limit.")
                    return f"drive_{file_id}.pdf", content, "application/pdf", file_id
                elif content.startswith(b"PK\x03\x04"):
                    if len(content) > MAX_GUIDELINE_SIZE_BYTES:
                        raise GuidelineExtractionError("Google Drive document exceeds 50MB limit.")
                    return (
                        f"drive_{file_id}.docx",
                        content,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        file_id,
                    )
    except GuidelineExtractionError:
        raise
    except Exception as exc:
        log.debug("HTTP Drive file download failed: %s", exc)

    # If all retrieval mechanisms failed
    raise GuidelineExtractionError(
        f"Unable to access Google Drive document '{file_id}'.",
        hint="Ensure the document link is accessible, set link sharing to 'Anyone with the link can view', or verify your Google Drive credentials.",
    )
