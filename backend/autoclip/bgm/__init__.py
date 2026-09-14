"""AL AMR BGM Vault package (Step 20).

Provides persistent audio asset storage, validation, metadata indexing, and operator
campaign music selection without autonomous AI classification or automated audio changes.
"""

from .metadata import SUPPORTED_AUDIO_EXTENSIONS, clean_tags, clean_text, infer_title
from .models import BGMAssetRecord, BGMFilterCriteria, BGMUploadMetadata
from .validation import (
    BGMCorruptAudioError,
    BGMFileSizeError,
    BGMUnavailableError,
    BGMUnsupportedFormatError,
    BGMValidationError,
    validate_audio_stream,
    validate_extension,
    validate_file_size,
)
from .vault import BGMVault

__all__ = [
    "BGMVault",
    "BGMAssetRecord",
    "BGMUploadMetadata",
    "BGMFilterCriteria",
    "BGMValidationError",
    "BGMUnsupportedFormatError",
    "BGMFileSizeError",
    "BGMCorruptAudioError",
    "BGMUnavailableError",
    "SUPPORTED_AUDIO_EXTENSIONS",
    "validate_audio_stream",
    "validate_extension",
    "validate_file_size",
    "clean_tags",
    "clean_text",
    "infer_title",
]
