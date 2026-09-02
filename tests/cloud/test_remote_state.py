"""Unit tests for RemoteStorageStateRepository."""

import pytest
from clipping.state.models import JobState, PipelineStage
from clipping.state.remote import RemoteStorageStateRepository
from clipping.storage.local import LocalStorageDriver


@pytest.mark.asyncio
async def test_remote_state_lifecycle(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    repo = RemoteStorageStateRepository(storage_driver=storage)

    job_id = "JOB_REMOTE_01"
    idemp_key = "IDEMP_REMOTE_01"

    # 1. Create Job
    job = await repo.create_job(
        job_id=job_id,
        campaign_id="CAMP_01",
        source_video_id="SRC_01",
        idempotency_key=idemp_key,
        metadata={"priority": "high"},
    )

    assert job.job_id == job_id
    assert job.current_state == JobState.CREATED
    assert await storage.exists(f"jobs/{job_id}/state.json") is True
    assert await storage.exists(f"jobs/by_idempotency/{idemp_key}.json") is True

    # 2. Retrieve Job by Idempotency Key
    retrieved = await repo.get_job_by_idempotency_key(idemp_key)
    assert retrieved is not None
    assert retrieved.job_id == job_id
    assert retrieved.metadata_json.get("priority") == "high"

    # 3. Transition to TRANSCRIBING (Stage: PERCEPTION)
    updated = await repo.update_job_state(
        job_id=job_id,
        new_state=JobState.TRANSCRIBING,
        new_stage=PipelineStage.PERCEPTION,
        reason="ASR started",
    )
    assert updated.current_state == JobState.TRANSCRIBING
    assert updated.current_stage == PipelineStage.PERCEPTION

    # 4. Transition to AWAITING_APPROVAL
    completed = await repo.update_job_state(
        job_id=job_id,
        new_state=JobState.AWAITING_APPROVAL,
        new_stage=PipelineStage.APPROVAL,
        reason="Pipeline finished",
    )
    assert completed.current_state == JobState.AWAITING_APPROVAL

    # 5. Verify History Audit Log
    history = await repo.get_job_history(job_id)
    assert len(history) == 3  # Initial + Transcribing + Awaiting Approval
    assert history[0].to_state == JobState.CREATED
    assert history[1].to_state == JobState.TRANSCRIBING
    assert history[2].to_state == JobState.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_remote_state_nonexistent_job(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    repo = RemoteStorageStateRepository(storage_driver=storage)

    job = await repo.get_job("JOB_NON_EXISTENT")
    assert job is None

    with pytest.raises(ValueError):
        await repo.update_job_state(
            job_id="JOB_NON_EXISTENT",
            new_state=JobState.TRANSCRIBING,
        )
