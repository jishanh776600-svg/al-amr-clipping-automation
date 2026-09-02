"""Abstract Base Class for Storage Drivers."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class FileMetadata(BaseModel):
    """Metadata describing a stored object in the vault."""
    model_config = ConfigDict(frozen=True)

    storage_key: str
    size_bytes: int = Field(ge=0)
    content_type: str = "application/octet-stream"
    checksum_sha256: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    modified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StorageDriver(ABC):
    """Abstract interface decoupling all media I/O from physical storage backends."""

    @abstractmethod
    async def upload(
        self,
        local_path: str,
        storage_key: str,
        content_type: Optional[str] = None
    ) -> FileMetadata:
        """Uploads a local file to the storage backend under the given logical key."""
        pass

    @abstractmethod
    async def upload_bytes(
        self,
        data: bytes,
        storage_key: str,
        content_type: Optional[str] = None
    ) -> FileMetadata:
        """Uploads in-memory bytes to the storage backend."""
        pass

    @abstractmethod
    async def download(self, storage_key: str, local_destination_path: str) -> str:
        """Downloads a file from the backend to a local destination path."""
        pass

    @abstractmethod
    async def download_bytes(self, storage_key: str) -> bytes:
        """Downloads a file from the backend directly into in-memory bytes."""
        pass

    @abstractmethod
    async def exists(self, storage_key: str) -> bool:
        """Checks if a file exists under the given logical key."""
        pass

    @abstractmethod
    async def delete(self, storage_key: str) -> bool:
        """Deletes a file from the storage backend."""
        pass

    @abstractmethod
    async def move(self, source_key: str, destination_key: str) -> FileMetadata:
        """Moves/renames a file from source_key to destination_key."""
        pass

    @abstractmethod
    async def copy(self, source_key: str, destination_key: str) -> FileMetadata:
        """Copies a file from source_key to destination_key."""
        pass

    @abstractmethod
    async def get_metadata(self, storage_key: str) -> FileMetadata:
        """Retrieves metadata and checksum for a stored file."""
        pass

    @abstractmethod
    async def list_files(self, prefix: str = "") -> List[FileMetadata]:
        """Lists all files matching an optional key prefix."""
        pass

    @abstractmethod
    async def checksum(self, storage_key: str) -> str:
        """Returns the SHA-256 hex digest of the file."""
        pass

    @abstractmethod
    async def create_folder(self, folder_key: str) -> bool:
        """Creates a logical folder/directory if applicable."""
        pass

    @abstractmethod
    async def get_access_url(self, storage_key: str, expires_in_seconds: int = 3600) -> str:
        """Returns a temporary accessible stream/download URL for the asset."""
        pass
