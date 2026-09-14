"""Server-Side Campaign URL Ingestion for AL AMR.

Safely fetches campaign web pages (Whop, Notion, landing pages, brand briefs)
with SSRF protection, clean HTML structure parsing, boilerplate filtering,
bounded timeouts, and user-facing error reporting.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from ..pipeline.source_acquisition.security import validate_remote_url, SourceAcquisitionError
from .extractor import GuidelineExtractionError

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
MAX_HTML_BODY_BYTES = 10 * 1024 * 1024  # 10 MB


@dataclass
class ExtractedUrlContent:
    """Result of server-side campaign URL extraction."""

    url: str
    title: str = ""
    description: str = ""
    headings: list[str] = field(default_factory=list)
    raw_text: str = ""
    status: str = "extracted"  # extracted, failed
    error: str | None = None
    word_count: int = 0
    char_count: int = 0
    status_code: int = 200


class _HTMLTextExtractor(HTMLParser):
    """Clean HTML text extractor that removes noise (scripts, styles, nav, footer)."""

    IGNORE_TAGS = frozenset({
        "script", "style", "nav", "footer", "header", "aside",
        "noscript", "svg", "form", "iframe", "button"
    })
    HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4"})

    def __init__(self) -> None:
        super().__init__()
        self._ignore_stack = 0
        self._current_tag = ""
        self.title = ""
        self.meta_desc = ""
        self.headings: list[str] = []
        self._text_chunks: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        self._current_tag = tag_lower
        if tag_lower in self.IGNORE_TAGS:
            self._ignore_stack += 1
            return

        if tag_lower == "title":
            self._in_title = True

        if tag_lower == "meta":
            attr_dict = {k.lower(): (v or "") for k, v in attrs}
            name = attr_dict.get("name", "").lower()
            prop = attr_dict.get("property", "").lower()
            content = attr_dict.get("content", "").strip()
            if content:
                if name == "description" or prop == "og:description":
                    if not self.meta_desc:
                        self.meta_desc = content
                elif prop == "og:title" and not self.title:
                    self.title = content

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.IGNORE_TAGS and self._ignore_stack > 0:
            self._ignore_stack -= 1
        if tag_lower == "title":
            self._in_title = False
        if tag_lower in {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}:
            self._text_chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignore_stack > 0:
            return
        cleaned = data.strip()
        if not cleaned:
            return

        if self._in_title and not self.title:
            self.title = cleaned
        elif self._current_tag in self.HEADING_TAGS:
            self.headings.append(cleaned)
            self._text_chunks.append(f"\n## {cleaned}\n")
        else:
            self._text_chunks.append(f" {cleaned} ")

    def get_clean_text(self) -> str:
        raw = "".join(self._text_chunks)
        # Normalize whitespace
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return "\n\n".join(lines)


def extract_campaign_url(url: str, timeout: float = 15.0) -> ExtractedUrlContent:
    """Securely fetches and extracts structured campaign text from a URL.

    Raises no unhandled exceptions; returns ExtractedUrlContent with error if failed.
    """
    if not url or not url.strip():
        return ExtractedUrlContent(
            url="",
            status="failed",
            error="No campaign URL was provided.",
        )

    clean_url = url.strip()

    # 1. SSRF & Security Validation
    try:
        validate_remote_url(clean_url)
    except SourceAcquisitionError as exc:
        log.warning("SSRF / security validation blocked campaign URL '%s': %s", clean_url, exc)
        return ExtractedUrlContent(
            url=clean_url,
            status="failed",
            error=f"Security check failed: {exc.message or str(exc)}",
        )
    except Exception as exc:
        return ExtractedUrlContent(
            url=clean_url,
            status="failed",
            error=f"Invalid URL: {exc}",
        )

    # 2. Fetch page content server-side
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=5.0),
            headers=headers,
            max_redirects=5,
        ) as client:
            resp = client.get(clean_url)
            status_code = resp.status_code

            # Re-validate destination URL after redirects
            try:
                validate_remote_url(str(resp.url))
            except SourceAcquisitionError as exc:
                return ExtractedUrlContent(
                    url=str(resp.url),
                    status="failed",
                    error="URL redirected to an unauthorized or private host.",
                )

            if status_code >= 400:
                return ExtractedUrlContent(
                    url=str(resp.url),
                    status="failed",
                    status_code=status_code,
                    error=f"Campaign page returned HTTP error {status_code}.",
                )

            content_bytes = resp.content
            if len(content_bytes) > MAX_HTML_BODY_BYTES:
                content_bytes = content_bytes[:MAX_HTML_BODY_BYTES]

            html_text = content_bytes.decode(resp.encoding or "utf-8", errors="replace")

    except httpx.TimeoutException:
        log.warning("Timeout retrieving campaign URL: %s", clean_url)
        return ExtractedUrlContent(
            url=clean_url,
            status="failed",
            error="Connection timed out while fetching campaign page.",
        )
    except httpx.RequestError as exc:
        log.warning("Network error retrieving campaign URL %s: %s", clean_url, exc)
        return ExtractedUrlContent(
            url=clean_url,
            status="failed",
            error="Could not connect to campaign server.",
        )
    except Exception as exc:
        log.error("Unexpected error retrieving campaign URL %s: %s", clean_url, exc)
        return ExtractedUrlContent(
            url=clean_url,
            status="failed",
            error="An error occurred while reading the campaign page.",
        )

    # 3. Parse HTML and extract textual requirements
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(html_text)
        text = extractor.get_clean_text()
    except Exception as exc:
        log.warning("HTML parser error on %s: %s", clean_url, exc)
        text = re.sub(r"<[^>]+>", " ", html_text)
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())

    title = extractor.title or urlparse(clean_url).netloc
    description = extractor.meta_desc

    word_count = len(re.findall(r"\b\w+\b", text))
    char_count = len(text)

    if not text.strip():
        return ExtractedUrlContent(
            url=clean_url,
            title=title,
            description=description,
            status="failed",
            error="Campaign web page contained no readable text content.",
            status_code=status_code,
        )

    return ExtractedUrlContent(
        url=clean_url,
        title=title,
        description=description,
        headings=extractor.headings[:15],
        raw_text=text,
        status="extracted",
        word_count=word_count,
        char_count=char_count,
        status_code=status_code,
    )
