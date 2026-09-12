"""Google Drive Persistent Storage Driver for AL AMR Clipping Automation.

Supports OAuth2 refresh-token authentication, nested folder resolution,
resumable uploads, downloads, and HTTP Range partial-content streaming.
"""

from __future__ import annotations

import io
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


@dataclass
class DriveFileMetadata:
    file_id: str
    name: str
    size_bytes: int
    mime_type: str
    web_view_link: str
    storage_key: str


class GoogleDriveStorage:
    """Persistent Google Drive storage driver for AL AMR media and job artifacts."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        root_folder_id: Optional[str] = None,
        scopes: Optional[list[str]] = None,
    ) -> None:
        self.client_id = client_id or os.getenv("GOOGLE_DRIVE_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("GOOGLE_DRIVE_CLIENT_SECRET", "")
        self.refresh_token = refresh_token or os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN", "")
        self.root_folder_id = root_folder_id or os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
        self.scopes = scopes

        self._folder_cache: Dict[str, str] = {}
        self._service: Any = None
        self._creds: Any = None

    @property
    def is_configured(self) -> bool:
        """Returns True if credentials required for Google Drive are present."""
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _get_service(self) -> Any:
        if not self.is_configured:
            raise RuntimeError(
                "Google Drive storage is not configured. Missing GOOGLE_DRIVE_CLIENT_ID, "
                "GOOGLE_DRIVE_CLIENT_SECRET, or GOOGLE_DRIVE_REFRESH_TOKEN."
            )
        if self._service is None:
            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build
            except ImportError as e:
                raise RuntimeError(
                    f"google-api-python-client is required for Google Drive storage: {e}"
                )

            self._creds = Credentials(
                token=None,
                refresh_token=self.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=self.scopes,
            )
            self._service = build("drive", "v3", credentials=self._creds, cache_discovery=False)
        return self._service

    def resolve_folder_id(self, folder_path: str) -> str:
        """Walks or creates folder hierarchy starting from root_folder_id."""
        clean_path = folder_path.strip("/").replace("\\", "/")
        if not clean_path:
            return self.root_folder_id

        if clean_path in self._folder_cache:
            return self._folder_cache[clean_path]

        service = self._get_service()
        current_parent = self.root_folder_id or "root"
        accumulated = ""

        for part in clean_path.split("/"):
            if not part:
                continue
            accumulated = f"{accumulated}/{part}" if accumulated else part
            if accumulated in self._folder_cache:
                current_parent = self._folder_cache[accumulated]
                continue

            query = (
                f"name = '{part}' and '{current_parent}' in parents and "
                "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            )
            resp = service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
            files = resp.get("files", [])

            if files:
                current_parent = files[0]["id"]
            else:
                body = {
                    "name": part,
                    "mimeType": "application/vnd.google-apps.folder",
                }
                if current_parent and current_parent != "root":
                    body["parents"] = [current_parent]
                created = service.files().create(body=body, fields="id").execute()
                current_parent = created["id"]

            self._folder_cache[accumulated] = current_parent

        return current_parent

    def find_file(self, storage_key: str) -> Optional[DriveFileMetadata]:
        """Searches for a file by its logical storage key (path/filename)."""
        clean_key = storage_key.strip("/").replace("\\", "/")
        parts = clean_key.split("/")
        filename = parts[-1]
        folder_path = "/".join(parts[:-1])

        parent_id = self.resolve_folder_id(folder_path)
        service = self._get_service()

        query = f"name = '{filename}' and '{parent_id}' in parents and trashed = false"
        resp = service.files().list(
            q=query,
            fields="files(id, name, size, mimeType, webViewLink)",
            pageSize=1,
        ).execute()

        files = resp.get("files", [])
        if not files:
            return None

        f = files[0]
        return DriveFileMetadata(
            file_id=f["id"],
            name=f.get("name", filename),
            size_bytes=int(f.get("size", 0)),
            mime_type=f.get("mimeType", "application/octet-stream"),
            web_view_link=f.get("webViewLink", f"https://drive.google.com/file/d/{f['id']}/view"),
            storage_key=storage_key,
        )

    def upload_file(
        self,
        local_path: Path | str,
        storage_key: str,
        content_type: Optional[str] = None,
        folder_type: Optional[str] = None,
        subfolder: Optional[str] = None,
    ) -> DriveFileMetadata:
        """Uploads local file to Drive, updating if it already exists."""
        local_path = Path(local_path)
        if not local_path.is_file():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        if folder_type:
            if subfolder:
                storage_key = f"{folder_type.strip('/')}/{subfolder.strip('/')}/{storage_key.strip('/')}"
            else:
                storage_key = f"{folder_type.strip('/')}/{storage_key.strip('/')}"

        service = self._get_service()
        clean_key = storage_key.strip("/").replace("\\", "/")
        parts = clean_key.split("/")
        filename = parts[-1]
        folder_path = "/".join(parts[:-1])

        parent_id = self.resolve_folder_id(folder_path)
        mime = content_type or mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"

        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True)

        existing = self.find_file(storage_key)
        if existing:
            updated = service.files().update(
                fileId=existing.file_id,
                media_body=media,
                fields="id, name, size, mimeType, webViewLink",
            ).execute()
            target_id = updated["id"]
            link = updated.get("webViewLink", existing.web_view_link)
            size = int(updated.get("size", local_path.stat().st_size))
        else:
            body: Dict[str, Any] = {
                "name": filename,
            }
            if parent_id and parent_id != "root":
                body["parents"] = [parent_id]
            created = service.files().create(
                body=body,
                media_body=media,
                fields="id, name, size, mimeType, webViewLink",
            ).execute()
            target_id = created["id"]
            link = created.get("webViewLink", f"https://drive.google.com/file/d/{target_id}/view")
            size = int(created.get("size", local_path.stat().st_size))

        return DriveFileMetadata(
            file_id=target_id,
            name=filename,
            size_bytes=size,
            mime_type=mime,
            web_view_link=link,
            storage_key=storage_key,
        )

    def upload_bytes(
        self,
        data: bytes,
        storage_key: str,
        content_type: Optional[str] = None,
    ) -> DriveFileMetadata:
        """Uploads in-memory bytes to Drive."""
        service = self._get_service()
        clean_key = storage_key.strip("/").replace("\\", "/")
        parts = clean_key.split("/")
        filename = parts[-1]
        folder_path = "/".join(parts[:-1])

        parent_id = self.resolve_folder_id(folder_path)
        mime = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=True)

        existing = self.find_file(storage_key)
        if existing:
            updated = service.files().update(
                fileId=existing.file_id,
                media_body=media,
                fields="id, name, size, mimeType, webViewLink",
            ).execute()
            target_id = updated["id"]
            link = updated.get("webViewLink", existing.web_view_link)
        else:
            body: Dict[str, Any] = {
                "name": filename,
            }
            if parent_id and parent_id != "root":
                body["parents"] = [parent_id]
            created = service.files().create(
                body=body,
                media_body=media,
                fields="id, name, size, mimeType, webViewLink",
            ).execute()
            target_id = created["id"]
            link = created.get("webViewLink", f"https://drive.google.com/file/d/{target_id}/view")

        return DriveFileMetadata(
            file_id=target_id,
            name=filename,
            size_bytes=len(data),
            mime_type=mime,
            web_view_link=link,
            storage_key=storage_key,
        )

    def download_file(self, file_id: str, dest_path: Path | str) -> Path:
        """Downloads file by file_id to local destination."""
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        service = self._get_service()
        from googleapiclient.http import MediaIoBaseDownload

        request = service.files().get_media(fileId=file_id)
        with open(dest_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        return dest_path

    def download_bytes(self, file_id: str) -> bytes:
        """Downloads file content as bytes."""
        service = self._get_service()
        from googleapiclient.http import MediaIoBaseDownload

        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return fh.getvalue()

    def stream_range(
        self,
        file_id: str,
        start_byte: int = 0,
        end_byte: Optional[int] = None,
        range_header: Optional[str] = None,
    ) -> Tuple[Any, int, dict[str, str]]:
        """Fetches a byte range from Google Drive using HTTP Range request.

        Returns:
            (content_generator, status_code, headers)
        """
        service = self._get_service()
        meta = service.files().get(fileId=file_id, fields="size, mimeType").execute()
        total_size = int(meta.get("size", 0))
        mime_type = meta.get("mimeType", "video/mp4")

        is_range = False
        if range_header and range_header.startswith("bytes="):
            is_range = True
            range_val = range_header.split("bytes=")[-1].strip()
            parts = range_val.split("-")
            if parts[0]:
                start_byte = int(parts[0])
            if len(parts) > 1 and parts[1]:
                end_byte = int(parts[1])

        if total_size > 0:
            if end_byte is None or end_byte >= total_size:
                end_byte = total_size - 1
        else:
            end_byte = end_byte or 0

        if start_byte > end_byte or start_byte < 0:
            start_byte = 0

        if self._creds and not self._creds.valid:
            import google.auth.transport.requests

            self._creds.refresh(google.auth.transport.requests.Request())

        token = getattr(self._creds, "token", "") if self._creds else ""
        headers = {
            "Authorization": f"Bearer {token}",
            "Range": f"bytes={start_byte}-{end_byte}",
        }

        url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"

        import httpx

        client = httpx.Client(timeout=60.0)
        req = client.build_request("GET", url, headers=headers)
        resp = client.send(req, stream=True)

        status_code = 206 if is_range else 200
        content_length = (
            (end_byte - start_byte + 1)
            if total_size > 0
            else int(resp.headers.get("Content-Length", 0))
        )

        response_headers = {
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
        }
        if is_range:
            response_headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{total_size}"

        def chunk_generator():
            try:
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    yield chunk
            finally:
                resp.close()
                client.close()

        return chunk_generator(), status_code, response_headers

