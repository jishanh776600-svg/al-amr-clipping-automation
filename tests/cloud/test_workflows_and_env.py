"""Unit tests for GitHub Actions workflow configurations and CLI runner."""

import os
import pytest
from clipping.cli.pipeline_runner import run_pipeline
from clipping.storage.local import LocalStorageDriver


def test_workflow_files_exist():
    workflows_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "workflows")
    smoke_test_yml = os.path.join(workflows_dir, "cloud_smoke_test.yml")
    orchestration_yml = os.path.join(workflows_dir, "pipeline_orchestration.yml")

    assert os.path.isfile(smoke_test_yml) is True
    assert os.path.isfile(orchestration_yml) is True

    # Validate contents contain required Ubuntu runner and system packages
    with open(smoke_test_yml, "r", encoding="utf-8") as f:
        smoke_content = f.read()
        assert "runs-on: ubuntu-latest" in smoke_content
        assert "ffmpeg" in smoke_content
        assert "fonts-liberation" in smoke_content

    with open(orchestration_yml, "r", encoding="utf-8") as f:
        orch_content = f.read()
        assert "runs-on: ubuntu-latest" in orch_content
        assert "clipping.cli.pipeline_runner" in orch_content


@pytest.mark.asyncio
async def test_pipeline_runner_execution(temp_vault_dir, monkeypatch):
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", temp_vault_dir)
    monkeypatch.setenv("STORAGE_DRIVER", "local")

    from tests.pipeline_mocks import (
        MockVideoIngestor,
        MockAudioPerceptionEngine,
        MockVideoUnderstandingEngine,
        MockClipDiscoveryEngine,
        MockVirtualCameraDirector,
        MockRenderOrchestrationEngine,
        MockQAEngine,
        MockTelegramApprovalGateway,
    )
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    monkeypatch.setattr("clipping.cli.pipeline_runner.get_storage_driver", lambda s: storage)
    monkeypatch.setattr("clipping.cli.pipeline_runner.get_ingestor", lambda s: MockVideoIngestor(storage))
    monkeypatch.setattr("clipping.cli.pipeline_runner.get_perception_engine", lambda s: MockAudioPerceptionEngine())
    monkeypatch.setattr("clipping.cli.pipeline_runner.get_vision_engine", lambda s: MockVideoUnderstandingEngine())
    monkeypatch.setattr("clipping.cli.pipeline_runner.get_discovery_engine", lambda s: MockClipDiscoveryEngine())
    monkeypatch.setattr("clipping.cli.pipeline_runner.get_camera_director", lambda s: MockVirtualCameraDirector())
    monkeypatch.setattr("clipping.cli.pipeline_runner.get_render_engine", lambda s: MockRenderOrchestrationEngine())
    monkeypatch.setattr("clipping.cli.pipeline_runner.get_qa_engine", lambda s: MockQAEngine())
    monkeypatch.setattr("clipping.cli.pipeline_runner.get_approval_gateway", lambda s, st: MockTelegramApprovalGateway())

    job_id = "JOB_CLI_RUNNER_TEST"
    exit_code = await run_pipeline(
        source_uri="https://www.youtube.com/watch?v=mock_video",
        campaign_id="CAMP_TEST",
        job_id=job_id,
    )

    assert exit_code == 0
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    assert await storage.exists(f"jobs/{job_id}/state.json") is True
