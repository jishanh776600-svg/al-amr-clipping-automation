"""Google Drive Storage Driver implementation using Google Drive API v3."""

import io
import json
import mimetypes
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from clipping.storage.base import StorageDriver, FileMetadata
from clipping.utils.checksum import compute_sha256_bytes


class GoogleDriveStorageDriver(StorageDriver):
    """
    StorageDriver backed by Google Drive (5 TB Vault).
    Decoupled via Google Drive API v3 service account or OAuth credentials.
    """

    def __init__(
        self,
        root_folder_id: str = "root",
        service_account_json: Optional[str] = None,
        service_account_file: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        drive_service: Optional[Any] = None,
    ):
        self.root_folder_id = root_folder_id
        self._service = drive_service
        self._folder_cache: Dict[str, str] = {"": root_folder_id}

        if self._service is None:
            if refresh_token:
                self._init_oauth_service(client_id, client_secret, refresh_token)
            elif service_account_json or service_account_file:
                self._init_service(service_account_json, service_account_file)

    def _init_oauth_service(self, client_id: Optional[str], client_secret: Optional[str], refresh_token: str) -> None:
        """Initializes Google Drive API service client with OAuth2 user refresh token credentials."""
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        scopes = ["https://www.googleapis.com/auth/drive"]
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def _init_service(self, json_str: Optional[str], file_path: Optional[str]) -> None:
        """Initializes Google Drive API service client with service account credentials."""
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        scopes = ["https://www.googleapis.com/auth/drive"]
        if json_str:
            info = json.loads(json_str)
            creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        elif file_path:
            creds = service_account.Credentials.from_service_account_file(file_path, scopes=scopes)
        else:
            raise ValueError("No valid Google credentials provided")

        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)

    @property
    def service(self) -> Any:
        if self._service is None:
            raise RuntimeError(
                "Google Drive service client is not initialized. "
                "Provide service_account_json or a mocked drive_service."
            )
        return self._service

    async def _resolve_folder_id(self, folder_path: str) -> str:
        """Resolves or creates nested folders in Google Drive, returning the target folder ID."""
        clean_path = folder_path.strip("/")
        if not clean_path:
            return self.root_folder_id

        if clean_path in self._folder_cache:
            return self._folder_cache[clean_path]

        parts = clean_path.split("/")
        current_parent = self.root_folder_id
        accumulated_path = ""

        for part in parts:
            accumulated_path = f"{accumulated_path}/{part}" if accumulated_path else part
            if accumulated_path in self._folder_cache:
                current_parent = self._folder_cache[accumulated_path]
                continue

            query = (
                f"name = '{part}' and '{current_parent}' in parents and "
                f"mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            )
            response = self.service.files().list(q=query, fields="files(id, name)").execute()
            files = response.get("files", [])

            if files:
                current_parent = files[0]["id"]
            else:
                file_metadata = {
                    "name": part,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [current_parent],
                }
                folder = self.service.files().create(body=file_metadata, fields="id").execute()
                current_parent = folder["id"]

            self._folder_cache[accumulated_path] = current_parent

        return current_parent

    async def _find_file(self, storage_key: str) -> Optional[Dict[str, Any]]:
        """Finds a file in Google Drive matching the logical storage key."""
        clean_key = storage_key.strip("/")
        parts = clean_key.split("/")
        filename = parts[-1]
        folder_path = "/".join(parts[:-1])

        parent_id = await self._resolve_folder_id(folder_path)
        query = f"name = '{filename}' and '{parent_id}' in parents and trashed = false"
        response = self.service.files().list(
            q=query,
            fields="files(id, name, size, mimeType, md5Checksum, sha256Checksum, createdTime, modifiedTime, webViewLink)",
        ).execute()

        files = response.get("files", [])
        return files[0] if files else None

    async def upload(
        self,
        local_path: str,
        storage_key: str,
        content_type: Optional[str] = None
    ) -> FileMetadata:
        with open(local_path, "rb") as f:
            data = f.read()
        mime = content_type or mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        return await self.upload_bytes(data, storage_key, content_type=mime)

    async def upload_bytes(
        self,
        data: bytes,
        storage_key: str,
        content_type: Optional[str] = None
    ) -> FileMetadata:
        from googleapiclient.http import MediaIoBaseUpload

        clean_key = storage_key.strip("/")
        parts = clean_key.split("/")
        filename = parts[-1]
        folder_path = "/".join(parts[:-1])

        parent_id = await self._resolve_folder_id(folder_path)
        mime = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

        existing = await self._find_file(storage_key)
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=True)

        if existing:
            file_id = existing["id"]
            self.service.files().update(fileId=file_id, media_body=media).execute()
        else:
            file_metadata = {
                "name": filename,
                "parents": [parent_id],
                "mimeType": mime,
            }
            self.service.files().create(body=file_metadata, media_body=media, fields="id").execute()

        checksum = compute_sha256_bytes(data)
        now = datetime.now(timezone.utc)

        return FileMetadata(
            storage_key=storage_key,
            size_bytes=len(data),
            content_type=mime,
            checksum_sha256=checksum,
            created_at=now,
            modified_at=now,
        )

    async def download(self, storage_key: str, local_destination_path: str) -> str:
        data = await self.download_bytes(storage_key)
        with open(local_destination_path, "wb") as f:
            f.write(data)
        return local_destination_path

    async def download_bytes(self, storage_key: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        file_obj = await self._find_file(storage_key)
        if not file_obj:
            raise FileNotFoundError(f"Object not found in Google Drive: {storage_key}")

        file_id = file_obj["id"]
        request = self.service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        return fh.getvalue()

    async def exists(self, storage_key: str) -> bool:
        file_obj = await self._find_file(storage_key)
        return file_obj is not None

    async def delete(self, storage_key: str) -> bool:
        file_obj = await self._find_file(storage_key)
        if file_obj:
            self.service.files().delete(fileId=file_obj["id"]).execute()
            return True
        return False

    async def move(self, source_key: str, destination_key: str) -> FileMetadata:
        data = await self.download_bytes(source_key)
        meta = await self.upload_bytes(data, destination_key)
        await self.delete(source_key)
        return meta

    async def copy(self, source_key: str, destination_key: str) -> FileMetadata:
        data = await self.download_bytes(source_key)
        return await self.upload_bytes(data, destination_key)

    async def get_metadata(self, storage_key: str) -> FileMetadata:
        file_obj = await self._find_file(storage_key)
        if not file_obj:
            raise FileNotFoundError(f"Object not found in Google Drive: {storage_key}")

        data = await self.download_bytes(storage_key)
        checksum = compute_sha256_bytes(data)

        return FileMetadata(
            storage_key=storage_key,
            size_bytes=int(file_obj.get("size", len(data))),
            content_type=file_obj.get("mimeType", "application/octet-stream"),
            checksum_sha256=checksum,
            created_at=datetime.now(timezone.utc),
            modified_at=datetime.now(timezone.utc),
        )

    async def list_files(self, prefix: str = "") -> List[FileMetadata]:
        # Simple recursive listing under root folder
        query = f"'{self.root_folder_id}' in parents and trashed = false"
        response = self.service.files().list(q=query, fields="files(id, name, mimeType, size)").execute()
        files = response.get("files", [])

        results = []
        for f in files:
            if f.get("mimeType") != "application/vnd.google-apps.folder":
                results.append(
                    FileMetadata(
                        storage_key=f["name"],
                        size_bytes=int(f.get("size", 0)),
                        content_type=f.get("mimeType", "application/octet-stream"),
                        checksum_sha256="",
                    )
                )
        return results

    async def checksum(self, storage_key: str) -> str:
        meta = await self.get_metadata(storage_key)
        return meta.checksum_sha256

    async def create_folder(self, folder_key: str) -> bool:
        await self._resolve_folder_id(folder_key)
        return True

    async def get_access_url(self, storage_key: str, expires_in_seconds: int = 3600) -> str:
        file_obj = await self._find_file(storage_key)
        if not file_obj:
            raise FileNotFoundError(f"Object not found in Google Drive: {storage_key}")
        return file_obj.get("webViewLink", f"https://drive.google.com/file/d/{file_obj['id']}/view")
