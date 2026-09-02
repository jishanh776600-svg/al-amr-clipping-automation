"""Pytest configuration and shared fixtures for Phase 1."""

import os
import sys
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock

# Ensure src is in sys.path
src_path = str(Path(__file__).parent.parent / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)


@pytest.fixture
def temp_vault_dir():
    """Provides a temporary directory for local storage driver testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def in_memory_db_url():
    """Provides an in-memory SQLite URL for state repository tests."""
    return "sqlite+aiosqlite:///:memory:"


@pytest.fixture
def mock_google_drive_service():
    """Creates a mock Google Drive v3 service client."""
    service = MagicMock()
    files_resource = MagicMock()
    service.files.return_value = files_resource
    return service
