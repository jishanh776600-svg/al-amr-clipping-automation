"""Unified Storage Driver Factory for Local and Cloud Environments."""

from typing import Optional
from clipping.config.settings import Settings, get_settings
from clipping.storage.base import StorageDriver
from clipping.storage.local import LocalStorageDriver
from clipping.storage.google_drive import GoogleDriveStorageDriver


def create_storage_driver(settings: Optional[Settings] = None) -> StorageDriver:
    """
    Instantiates the appropriate StorageDriver based on application settings.
    Supports both OAuth2 Refresh Token (personal Drive) and Service Account credentials.
    """
    cfg = settings or get_settings()

    if cfg.STORAGE_DRIVER == "gdrive" and cfg.GOOGLE_DRIVE_ROOT_FOLDER_ID:
        if cfg.GOOGLE_DRIVE_REFRESH_TOKEN:
            return GoogleDriveStorageDriver(
                root_folder_id=cfg.GOOGLE_DRIVE_ROOT_FOLDER_ID,
                client_id=cfg.GOOGLE_DRIVE_CLIENT_ID,
                client_secret=cfg.GOOGLE_DRIVE_CLIENT_SECRET.get_secret_value() if cfg.GOOGLE_DRIVE_CLIENT_SECRET else None,
                refresh_token=cfg.GOOGLE_DRIVE_REFRESH_TOKEN.get_secret_value(),
            )
        return GoogleDriveStorageDriver(
            root_folder_id=cfg.GOOGLE_DRIVE_ROOT_FOLDER_ID,
            service_account_json=cfg.GOOGLE_SERVICE_ACCOUNT_JSON.get_secret_value() if cfg.GOOGLE_SERVICE_ACCOUNT_JSON else None,
            service_account_file=cfg.GOOGLE_APPLICATION_CREDENTIALS,
        )

    return LocalStorageDriver(root_dir=cfg.LOCAL_STORAGE_ROOT)
