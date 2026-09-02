"""Local Filesystem Storage Driver implementation."""

import os
import shutil
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from clipping.storage.base import StorageDriver, FileMetadata
from clipping.utils.checksum import compute_sha256_bytes, compute_sha256_file


class LocalStorageDriver(StorageDriver):
    """StorageDriver backed by a local filesystem or mounted persistent volume."""

    def __init__(self, root_dir: str = "./project_vault"):
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, storage_key: str) -> Path:
        """Resolves a logical storage key to an absolute Path safely."""
        clean_key = storage_key.lstrip("/").replace("\\", "/")
        full_path = (self.root_dir / clean_key).resolve()
        if not str(full_path).startswith(str(self.root_dir)):
            raise ValueError(f"Directory traversal detected for key: {storage_key}")
        return full_path

    async def upload(
        self,
        local_path: str,
        storage_key: str,
        content_type: Optional[str] = None
    ) -> FileMetadata:
        src = Path(local_path).resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Source file not found: {local_path}")

        dest = self._resolve_path(storage_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

        return await self.get_metadata(storage_key)

    async def upload_bytes(
        self,
        data: bytes,
        storage_key: str,
        content_type: Optional[str] = None
    ) -> FileMetadata:
        dest = self._resolve_path(storage_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

        return await self.get_metadata(storage_key)

    async def download(self, storage_key: str, local_destination_path: str) -> str:
        src = self._resolve_path(storage_key)
        if not src.is_file():
            raise FileNotFoundError(f"Object not found in storage: {storage_key}")

        dest = Path(local_destination_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return str(dest)

    async def download_bytes(self, storage_key: str) -> bytes:
        src = self._resolve_path(storage_key)
        if not src.is_file():
            raise FileNotFoundError(f"Object not found in storage: {storage_key}")
        return src.read_bytes()

    async def exists(self, storage_key: str) -> bool:
        src = self._resolve_path(storage_key)
        return src.is_file()

    async def delete(self, storage_key: str) -> bool:
        src = self._resolve_path(storage_key)
        if src.is_file():
            src.unlink()
            return True
        return False

    async def move(self, source_key: str, destination_key: str) -> FileMetadata:
        src = self._resolve_path(source_key)
        if not src.is_file():
            raise FileNotFoundError(f"Source object not found: {source_key}")

        dest = self._resolve_path(destination_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dest)
        return await self.get_metadata(destination_key)

    async def copy(self, source_key: str, destination_key: str) -> FileMetadata:
        src = self._resolve_path(source_key)
        if not src.is_file():
            raise FileNotFoundError(f"Source object not found: {source_key}")

        dest = self._resolve_path(destination_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return await self.get_metadata(destination_key)

    async def get_metadata(self, storage_key: str) -> FileMetadata:
        src = self._resolve_path(storage_key)
        if not src.is_file():
            raise FileNotFoundError(f"Object not found: {storage_key}")

        stat = src.stat()
        mime_type, _ = mimetypes.guess_type(str(src))
        checksum = compute_sha256_file(str(src))

        return FileMetadata(
            storage_key=storage_key,
            size_bytes=stat.st_size,
            content_type=mime_type or "application/octet-stream",
            checksum_sha256=checksum,
            created_at=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )

    async def list_files(self, prefix: str = "") -> List[FileMetadata]:
        prefix_clean = prefix.lstrip("/").replace("\\", "/")
        results: List[FileMetadata] = []

        for p in self.root_dir.rglob("*"):
            if p.is_file():
                rel_key = p.relative_to(self.root_dir).as_posix()
                if not prefix_clean or rel_key.startswith(prefix_clean):
                    results.append(await self.get_metadata(rel_key))

        return results

    async def checksum(self, storage_key: str) -> str:
        meta = await self.get_metadata(storage_key)
        return meta.checksum_sha256

    async def create_folder(self, folder_key: str) -> bool:
        folder_path = self._resolve_path(folder_key)
        folder_path.mkdir(parents=True, exist_ok=True)
        return True

    async def get_access_url(self, storage_key: str, expires_in_seconds: int = 3600) -> str:
        # For local driver, returns a file:// URI
        src = self._resolve_path(storage_key)
        if not src.is_file():
            raise FileNotFoundError(f"Object not found: {storage_key}")
        return src.as_uri()
