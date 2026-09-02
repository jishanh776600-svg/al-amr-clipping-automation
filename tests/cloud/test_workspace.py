"""Unit tests for WorkerScratchWorkspace."""

import os
import pytest
from clipping.core.workspace import WorkerScratchWorkspace


def test_workspace_lifecycle_and_cleanup():
    job_id = "JOB_TEST_WORKSPACE_01"
    ws = WorkerScratchWorkspace(job_id=job_id)

    with ws:
        workspace_dir = ws.workspace_dir
        assert os.path.isdir(workspace_dir) is True

        # Resolve file path inside workspace
        test_file = ws.get_path("media/source.mp4")
        assert test_file.startswith(workspace_dir)

        # Write test data
        with open(test_file, "wb") as f:
            f.write(b"SAMPLE_VIDEO_DATA_12345")

        assert os.path.isfile(test_file) is True
        assert ws.get_current_size() == len(b"SAMPLE_VIDEO_DATA_12345")

    # After exiting context, directory must be automatically cleaned up
    assert os.path.exists(workspace_dir) is False


def test_workspace_directory_traversal_defense():
    ws = WorkerScratchWorkspace(job_id="JOB_SECURITY")
    with ws:
        with pytest.raises(ValueError):
            ws.get_path("../../etc/shadow")

        with pytest.raises(ValueError):
            ws.get_path("/absolute/root/override")
