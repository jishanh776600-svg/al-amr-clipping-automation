"""Source reference abstraction for video ingestion."""

import os
import re
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator
from clipping.ingestion.exceptions import InvalidSourceError


class SourceType(str, Enum):
    YOUTUBE = "youtube"
    DIRECT_URL = "direct_url"
    GDRIVE = "gdrive"
    LOCAL_FILE = "local_file"
    CUSTOM = "custom"


class SourceReference(BaseModel):
    """Abstract representation of an external or cloud video source."""
    model_config = ConfigDict(frozen=True)

    source_type: SourceType
    uri: str = Field(..., min_length=1)
    title_hint: Optional[str] = None
    extra_params: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise InvalidSourceError("Source URI cannot be empty")
        return v_clean

    @classmethod
    def from_uri(cls, uri: str) -> "SourceReference":
        """Factory method to auto-detect source type from URI."""
        uri_clean = uri.strip()
        if not uri_clean:
            raise InvalidSourceError("Empty URI provided")

        # Local file detection
        clean_path = uri_clean.replace("file://", "").strip()
        if os.path.exists(clean_path) or uri_clean.startswith("file://") or re.match(r"^[a-zA-Z]:[\\/]", clean_path):
            return cls(source_type=SourceType.LOCAL_FILE, uri=clean_path)

        # YouTube detection
        youtube_regex = r"(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})"
        if re.search(youtube_regex, uri_clean):
            return cls(source_type=SourceType.YOUTUBE, uri=uri_clean)

        # Google Drive detection
        if "drive.google.com" in uri_clean or uri_clean.startswith("gdrive://"):
            return cls(source_type=SourceType.GDRIVE, uri=uri_clean)

        # Direct HTTP/HTTPS URL
        if uri_clean.startswith("http://") or uri_clean.startswith("https://"):
            return cls(source_type=SourceType.DIRECT_URL, uri=uri_clean)

        # Default fallback
        return cls(source_type=SourceType.CUSTOM, uri=uri_clean)
