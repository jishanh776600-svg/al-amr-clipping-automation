"""YouTube Metadata Generation, Sanitization, and Validation."""

import re
from datetime import datetime
from typing import List, Optional
from clipping.publishing.models import YouTubeVideoMetadata, PrivacyStatus


def sanitize_youtube_text(text: str) -> str:
    """
    Strips angle brackets '<' and '>' which YouTube Data API explicitly rejects.
    Normalizes excessive whitespace.
    """
    cleaned = re.sub(r"[<>]", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class YouTubeMetadataBuilder:
    """Constructs compliant, deterministic YouTube Shorts metadata."""

    @staticmethod
    def build(
        title: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE,
        scheduled_publish_at: Optional[datetime] = None,
    ) -> YouTubeVideoMetadata:
        # 1. Clean Title & Ensure #Shorts
        clean_title = sanitize_youtube_text(title)
        if "#Shorts" not in clean_title and "#shorts" not in clean_title:
            if len(clean_title) + 8 <= 100:
                clean_title = f"{clean_title} #Shorts"
            else:
                clean_title = f"{clean_title[:91]} #Shorts"

        # Final cap at 100 chars
        clean_title = clean_title[:100].strip()
        if not clean_title:
            clean_title = "Vertical Video #Shorts"

        # 2. Clean Description
        clean_desc = sanitize_youtube_text(description)
        if "#Shorts" not in clean_desc:
            clean_desc = f"{clean_desc}\n\n#Shorts #Viral" if clean_desc else "#Shorts #Viral"
        clean_desc = clean_desc[:5000].strip()

        # 3. Clean and Cap Tags (YouTube API enforces ~500 total chars across tags)
        tag_set = ["Shorts", "VerticalVideo"]
        if tags:
            for t in tags:
                sanitized_tag = sanitize_youtube_text(t)
                if sanitized_tag and sanitized_tag not in tag_set:
                    tag_set.append(sanitized_tag)

        # Cap total tags length
        final_tags: List[str] = []
        total_len = 0
        for t in tag_set:
            if total_len + len(t) + 1 <= 450:
                final_tags.append(t)
                total_len += len(t) + 1
            else:
                break

        return YouTubeVideoMetadata(
            title=clean_title,
            description=clean_desc,
            tags=final_tags,
            privacy_status=privacy_status,
            publish_at=scheduled_publish_at,
        )
