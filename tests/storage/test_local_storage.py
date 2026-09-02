"""Unit tests for LocalStorageDriver."""

import pytest
from pathlib import Path
from clipping.storage.local import LocalStorageDriver


@pytest.mark.asyncio
async def test_local_storage_lifecycle(temp_vault_dir):
    driver = LocalStorageDriver(root_dir=temp_vault_dir)

    # 1. Upload bytes
    data = b"Sample video test binary data"
    key = "sources/VID_01/audio.wav"
    meta = await driver.upload_bytes(data, key, content_type="audio/wav")

    assert meta.storage_key == key
    assert meta.size_bytes == len(data)
    assert meta.checksum_sha256 is not None

    # 2. Exists & Download bytes
    assert await driver.exists(key) is True
    retrieved = await driver.download_bytes(key)
    assert retrieved == data

    # 3. Checksum
    cs = await driver.checksum(key)
    assert cs == meta.checksum_sha256

    # 4. Copy & Move
    copy_key = "sources/VID_01/audio_backup.wav"
    await driver.copy(key, copy_key)
    assert await driver.exists(copy_key) is True

    moved_key = "sources/VID_01/audio_renamed.wav"
    await driver.move(copy_key, moved_key)
    assert await driver.exists(copy_key) is False
    assert await driver.exists(moved_key) is True

    # 5. List files
    files = await driver.list_files(prefix="sources/VID_01")
    assert len(files) == 2

    # 6. Delete
    deleted = await driver.delete(moved_key)
    assert deleted is True
    assert await driver.exists(moved_key) is False


@pytest.mark.asyncio
async def test_directory_traversal_defense(temp_vault_dir):
    driver = LocalStorageDriver(root_dir=temp_vault_dir)
    with pytest.raises(ValueError, match="Directory traversal"):
        await driver.upload_bytes(b"bad", "../../etc/passwd")
