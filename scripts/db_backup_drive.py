#!/usr/bin/env python3
"""
AL AMR — SQLite Database Backup to Google Drive

Performs a safe WAL checkpoint and uploads autoclip.db to Google Drive.
Run this:
  - Before any deployment
  - On a periodic schedule (cron / systemd timer)
  - After major state changes (jobs completed, etc.)

Usage:
    python scripts/db_backup_drive.py [--label LABEL]

Environment variables required (same as control plane):
    AUTOCLIP_HOME            Path to data directory (default: /data)
    GOOGLE_DRIVE_CLIENT_ID
    GOOGLE_DRIVE_CLIENT_SECRET
    GOOGLE_DRIVE_REFRESH_TOKEN
    GOOGLE_DRIVE_ROOT_FOLDER_ID

NEVER exposes credentials in logs or output.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import shutil
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("db_backup")


def _resolve_autoclip_home() -> Path:
    override = os.environ.get("AUTOCLIP_HOME")
    if override:
        return Path(override).expanduser().resolve()
    data = Path("/data")
    if data.is_dir() and os.access(data, os.W_OK):
        return data
    return (Path.home() / ".autoclip").resolve()


def checkpoint_wal(db_path: Path) -> None:
    """Flush all WAL frames into the main database file."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
        log.info("WAL checkpoint complete: %s", db_path)
    except Exception as exc:
        log.warning("WAL checkpoint failed (non-fatal): %s", exc)
    finally:
        conn.close()


def verify_integrity(db_path: Path) -> bool:
    """Run SQLite integrity_check. Returns True if ok."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        ok = result and result[0] == "ok"
        if ok:
            log.info("SQLite integrity_check: ok")
        else:
            log.error("SQLite integrity_check FAILED: %s", result)
        return bool(ok)
    except Exception as exc:
        log.error("integrity_check error: %s", exc)
        return False
    finally:
        conn.close()


def make_backup_copy(db_path: Path, label: str) -> Path:
    """Copy database to a timestamped backup file next to the original."""
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{label}" if label else ""
    backup_name = f"autoclip-backup-{ts}{suffix}.db"
    backup_path = db_path.parent / backup_name
    shutil.copy2(str(db_path), str(backup_path))
    size_kb = backup_path.stat().st_size // 1024
    log.info("Backup copy created: %s (%d KB)", backup_path.name, size_kb)
    return backup_path


def _get_drive_service():
    """Build Google Drive API service from environment credentials."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN", "").strip()

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(
            "Missing Google Drive credentials: "
            "GOOGLE_DRIVE_CLIENT_ID, GOOGLE_DRIVE_CLIENT_SECRET, GOOGLE_DRIVE_REFRESH_TOKEN"
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_to_drive(backup_path: Path) -> str:
    """Upload backup file to Google Drive. Returns the file ID."""
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        log.error("google-api-python-client not installed. Skipping Drive upload.")
        return ""

    root_folder_id = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip()

    service = _get_drive_service()

    # Find or create an 'autoclip-db-backups' folder inside root
    backup_folder_id = _ensure_backup_folder(service, root_folder_id)

    file_metadata = {
        "name": backup_path.name,
        "parents": [backup_folder_id] if backup_folder_id else ([root_folder_id] if root_folder_id else []),
        "description": f"AL AMR autoclip.db backup — {backup_path.name}",
    }

    media = MediaFileUpload(
        str(backup_path),
        mimetype="application/x-sqlite3",
        resumable=True,
    )

    result = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,size",
    ).execute()

    file_id = result.get("id", "")
    size_bytes = int(result.get("size", 0))
    log.info(
        "Uploaded to Google Drive: name=%s id=%s size=%d bytes — credentials NOT logged",
        result.get("name"),
        file_id,
        size_bytes,
    )
    return file_id


def _ensure_backup_folder(service, root_folder_id: str) -> str:
    """Find or create 'autoclip-db-backups' folder. Returns folder ID."""
    try:
        query = "name='autoclip-db-backups' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if root_folder_id:
            query += f" and '{root_folder_id}' in parents"

        resp = service.files().list(q=query, fields="files(id,name)", pageSize=1).execute()
        files = resp.get("files", [])
        if files:
            folder_id = files[0]["id"]
            log.info("Found existing Drive backup folder: %s", folder_id)
            return folder_id

        # Create it
        folder_meta = {
            "name": "autoclip-db-backups",
            "mimeType": "application/vnd.google-apps.folder",
        }
        if root_folder_id:
            folder_meta["parents"] = [root_folder_id]

        folder = service.files().create(body=folder_meta, fields="id").execute()
        folder_id = folder["id"]
        log.info("Created Drive backup folder: %s", folder_id)
        return folder_id
    except Exception as exc:
        log.warning("Could not ensure Drive backup folder: %s. Uploading to root.", exc)
        return root_folder_id or ""


def prune_old_local_backups(db_dir: Path, keep: int = 5) -> None:
    """Remove old local backup files, keeping the most recent N."""
    backups = sorted(db_dir.glob("autoclip-backup-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    to_delete = backups[keep:]
    for old in to_delete:
        try:
            old.unlink()
            log.info("Pruned old local backup: %s", old.name)
        except Exception as exc:
            log.warning("Could not prune %s: %s", old.name, exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="AL AMR SQLite → Google Drive backup")
    parser.add_argument("--label", default="", help="Optional label appended to backup filename")
    parser.add_argument("--skip-drive", action="store_true", help="Skip Google Drive upload (local backup only)")
    parser.add_argument("--no-integrity-check", action="store_true", help="Skip integrity check (faster)")
    args = parser.parse_args()

    home = _resolve_autoclip_home()
    db_path = home / "autoclip.db"

    if not db_path.is_file():
        # Try legacy name
        legacy = home / "clipforge.db"
        if legacy.is_file():
            db_path = legacy
        else:
            log.error("Database not found at %s or %s. Nothing to back up.", home / "autoclip.db", legacy)
            return 1

    log.info("Backing up: %s", db_path)

    # 1. WAL checkpoint
    checkpoint_wal(db_path)

    # 2. Integrity check
    if not args.no_integrity_check:
        if not verify_integrity(db_path):
            log.error("Aborting backup: integrity check failed.")
            return 2

    # 3. Local backup copy
    backup_path = make_backup_copy(db_path, args.label)

    # 4. Upload to Google Drive
    if not args.skip_drive:
        try:
            file_id = upload_to_drive(backup_path)
            if not file_id:
                log.warning("Drive upload returned no file ID — may have failed silently.")
        except Exception as exc:
            log.error("Drive upload failed: %s", exc)
            log.info("Local backup preserved at: %s", backup_path)
            # Do not treat drive failure as fatal — local backup is still valid
    else:
        log.info("Skipping Drive upload (--skip-drive)")

    # 5. Prune old local backups
    prune_old_local_backups(db_path.parent, keep=5)

    log.info("Backup complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
