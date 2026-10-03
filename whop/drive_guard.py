"""Google Drive Durable Artifact Guard (Step 10.1).

Validates that Google Drive file IDs and artifact references represent genuine,
durable cloud uploads, strictly rejecting synthetic templates, mock placeholders,
and ephemeral IDs.

Invariants:
1. Drive file ID must match canonical Google Drive format (25-65 alphanumeric/hyphen/underscore chars).
2. Explicitly rejects known synthetic patterns ('1DriveFileId_*', 'drive_id_*', 'mock', 'fake', 'stub').
3. Verifies that all 5 required submission clips have distinct, non-duplicate Drive IDs.
4. Optionally performs authoritative remote API validation against Google Drive.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Union

log = logging.getLogger(__name__)

# Canonical Google Drive resource ID pattern
DRIVE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{25,65}$")

# Disallowed synthetic / template patterns
SYNTHETIC_DRIVE_PATTERNS = [
    re.compile(r"^1DriveFileId_", re.IGNORECASE),
    re.compile(r"^drive_id_", re.IGNORECASE),
    re.compile(r"mock", re.IGNORECASE),
    re.compile(r"fake", re.IGNORECASE),
    re.compile(r"stub", re.IGNORECASE),
    re.compile(r"dummy", re.IGNORECASE),
    re.compile(r"placeholder", re.IGNORECASE),
    re.compile(r"sample_drive", re.IGNORECASE),
    re.compile(r"test_drive", re.IGNORECASE),
    re.compile(r"clip_\d+", re.IGNORECASE),
]


class DriveGuardError(ValueError):
    """Raised when Google Drive artifact validation fails."""
    pass


def is_real_drive_file_id(
    drive_id: Optional[str],
    verify_remote: bool = False,
    storage: Optional[Any] = None,
) -> bool:
    """Check if a Drive file ID satisfies real Google Drive format and is not synthetic."""
    if not drive_id or not isinstance(drive_id, str):
        return False

    clean_id = drive_id.strip()
    if not clean_id:
        return False

    # Check against synthetic templates
    for pattern in SYNTHETIC_DRIVE_PATTERNS:
        if pattern.search(clean_id):
            return False

    # Must match canonical format
    if not DRIVE_ID_REGEX.match(clean_id):
        return False

    if verify_remote:
        if storage is None:
            try:
                from backend.autoclip.storage.drive import GoogleDriveStorage
                storage = GoogleDriveStorage()
            except Exception:
                pass

        if storage and hasattr(storage, "_get_service"):
            try:
                service = storage._get_service()
                meta = service.files().get(fileId=clean_id, fields="id, name, trashed").execute()
                if not meta or meta.get("trashed", False):
                    return False
            except Exception as exc:
                log.warning("Remote Drive verification failed for ID '%s': %s", clean_id, exc)
                return False

    return True


def assert_real_drive_artifacts(
    clips_or_ids: Union[List[str], List[Any]],
    storage: Optional[Any] = None,
    verify_remote: bool = False,
    require_five: bool = True,
) -> List[str]:
    """Assert that all items possess valid, real Google Drive file IDs.
    
    Accepts a list of Drive ID strings or objects/dicts with attribute/key 'drive_file_id'.
    Raises DriveGuardError if any ID is missing, synthetic, or duplicated.
    """
    if not clips_or_ids:
        raise DriveGuardError("No clips or Drive IDs provided for Drive durability verification.")

    drive_ids: List[str] = []
    for item in clips_or_ids:
        if isinstance(item, str):
            did = item.strip()
        elif isinstance(item, dict):
            did = str(item.get("drive_file_id", "")).strip()
        elif hasattr(item, "drive_file_id"):
            did = str(getattr(item, "drive_file_id", "")).strip()
        else:
            raise DriveGuardError(f"Cannot extract drive_file_id from item: {type(item)}")

        if not did:
            raise DriveGuardError(f"Missing drive_file_id for clip in submission batch: {item}")

        if not is_real_drive_file_id(did, verify_remote=verify_remote, storage=storage):
            raise DriveGuardError(
                f"Synthetic or invalid Google Drive file ID detected: '{did}'. "
                f"Production submissions must use genuine, uploaded Google Drive file IDs."
            )

        drive_ids.append(did)

    if require_five and len(drive_ids) != 5:
        raise DriveGuardError(
            f"Whop submission invariant requires exactly 5 durable Drive file IDs, got {len(drive_ids)}."
        )

    if len(set(drive_ids)) != len(drive_ids):
        raise DriveGuardError(
            f"Duplicate Google Drive file IDs detected in batch: {drive_ids}"
        )

    return drive_ids
