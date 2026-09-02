"""Unit tests for StateRepository and persistent state machine."""

import pytest
from clipping.state.repository import SqlAlchemyStateRepository
from clipping.state.models import JobState, PipelineStage


@pytest.mark.asyncio
async def test_job_lifecycle_and_transitions(in_memory_db_url):
    repo = SqlAlchemyStateRepository(in_memory_db_url)
    await repo.init_db()

    # 1. Create Job
    job = await repo.create_job(
        job_id="JOB_001",
        campaign_id="CAMP_001",
        source_video_id="VID_001",
        idempotency_key="idemp_camp001_vid001",
        metadata={"custom_flag": "test"},
    )
    assert job.job_id == "JOB_001"
    assert job.current_state == JobState.CREATED
    assert job.current_stage == PipelineStage.INITIALIZATION
    assert job.retry_count == 0

    # 2. Query by Idempotency Key
    existing = await repo.get_job_by_idempotency_key("idemp_camp001_vid001")
    assert existing is not None
    assert existing.job_id == "JOB_001"

    # 3. Transition: CREATED -> TRANSCRIBING
    updated = await repo.update_job_state(
        job_id="JOB_001",
        new_state=JobState.TRANSCRIBING,
        new_stage=PipelineStage.PERCEPTION,
        reason="Started audio transcription",
    )
    assert updated.current_state == JobState.TRANSCRIBING
    assert updated.current_stage == PipelineStage.PERCEPTION

    # 4. Transition: TRANSCRIBING -> AWAITING_APPROVAL (Durable pause)
    await repo.update_job_state(
        job_id="JOB_001",
        new_state=JobState.AWAITING_APPROVAL,
        new_stage=PipelineStage.APPROVAL,
        reason="Render and QA completed, dispatched Telegram message",
        metadata={"telegram_msg_id": 9988},
    )

    # 5. Transition: AWAITING_APPROVAL -> APPROVED (Human tap)
    approved = await repo.update_job_state(
        job_id="JOB_001",
        new_state=JobState.APPROVED,
        reason="Human approved short via Telegram inline button",
    )
    assert approved.current_state == JobState.APPROVED

    # 6. Check Transition History
    transitions = await repo.list_transitions("JOB_001")
    assert len(transitions) == 3
    assert transitions[0].from_state == JobState.CREATED
    assert transitions[0].to_state == JobState.TRANSCRIBING
    assert transitions[1].to_state == JobState.AWAITING_APPROVAL
    assert transitions[2].to_state == JobState.APPROVED

    # 7. Increment Retry Count
    retries = await repo.increment_retry_count("JOB_001")
    assert retries == 1
    job_after_retry = await repo.get_job("JOB_001")
    assert job_after_retry.retry_count == 1
