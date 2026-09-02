"""Unit tests for GoogleDriveStorageDriver using mocked Google Drive API v3."""

import pytest
from unittest.mock import MagicMock
from clipping.storage.google_drive import GoogleDriveStorageDriver


@pytest.mark.asyncio
async def test_google_drive_upload_and_exists(mock_google_drive_service):
    # Mock folder listing & creation
    list_mock = MagicMock()
    list_mock.execute.return_value = {"files": []}
    mock_google_drive_service.files().list.return_value = list_mock

    create_mock = MagicMock()
    create_mock.execute.return_value = {"id": "mock_file_id_123"}
    mock_google_drive_service.files().create.return_value = create_mock

    driver = GoogleDriveStorageDriver(
        root_folder_id="root_vault_folder",
        drive_service=mock_google_drive_service,
    )

    data = b"Simulated MP4 binary stream"
    key = "campaigns/CAMP_01/raw_spec.pdf"
    meta = await driver.upload_bytes(data, key, content_type="application/pdf")

    assert meta.storage_key == key
    assert meta.size_bytes == len(data)
    assert meta.checksum_sha256 is not None

    # Verify exists()
    list_mock.execute.return_value = {"files": [{"id": "mock_file_id_123", "name": "raw_spec.pdf"}]}
    assert await driver.exists(key) is True

    # Verify delete()
    delete_mock = MagicMock()
    delete_mock.execute.return_value = {}
    mock_google_drive_service.files().delete.return_value = delete_mock
    assert await driver.delete(key) is True
