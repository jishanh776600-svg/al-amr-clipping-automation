"""Regression test suite for SQLite foreign key integrity on campaign_evaluations table."""

import pytest
import sqlite3
from autoclip.campaign.evaluator import CampaignEvaluator
from autoclip.campaign.models import CampaignBrief
from autoclip.db import connection, reset_connections
from autoclip.db.models import CampaignEvaluationRow, Clip, Job, Source, new_id
from autoclip.pipeline.transcript import Word
from autoclip.db import store




def test_evaluator_preserves_clip_id():
    """Verify evaluate_candidate properly populates clip_id on CandidateEvaluation."""
    campaign = CampaignBrief(
        campaign_id="test_camp",
        name="Test Campaign",
        minimum_duration=5.0,
        maximum_duration=60.0,
        minimum_viral_score=0.0,
        minimum_content_density=0.1,
    )
    evaluator = CampaignEvaluator(campaign)
    words = [
        Word(text="This", start=0.0, end=0.5),
        Word(text="is", start=0.5, end=1.0),
        Word(text="amazing", start=1.0, end=2.0),
    ]
    test_clip_id = new_id()
    ev = evaluator.evaluate_candidate(
        candidate_id=test_clip_id,
        clip_id=test_clip_id,
        start_s=0.0,
        end_s=2.0,
        words=words,
        base_viral_score=8.5,
    )

    assert ev.clip_id == test_clip_id
    assert ev.candidate_id == test_clip_id
    assert ev.campaign_id == "test_camp"



def test_store_rejects_empty_clip_id(initialised_db):
    """Verify create_campaign_evaluation fails early when clip_id is empty or whitespace."""
    row = CampaignEvaluationRow(
        clip_id="",
        campaign_id="test_camp",
        approved=True,
        final_score=8.0,
        hook_score=8.0,
        cta_score=8.0,
        viral_score=8.0,
        density_score=8.0,
    )
    with pytest.raises(ValueError, match="clip_id cannot be empty"):
        store.create_campaign_evaluation(row)

    row_whitespace = CampaignEvaluationRow(
        clip_id="   ",
        campaign_id="test_camp",
        approved=True,
    )
    with pytest.raises(ValueError, match="clip_id cannot be empty"):
        store.create_campaign_evaluation(row_whitespace)


def test_create_campaign_evaluation_with_valid_fk(initialised_db):
    """Verify create_campaign_evaluation succeeds when referenced clip exists and passes foreign_key_check."""
    source_id = new_id()
    job_id = new_id()
    clip_id = new_id()

    store.create_source(Source(id=source_id, type="upload", path="test.mp4", duration_s=30.0))
    store.create_job(Job(id=job_id, source_id=source_id))
    store.replace_clips(job_id, [Clip(id=clip_id, job_id=job_id, start_s=0.0, end_s=15.0)])

    row = CampaignEvaluationRow(
        clip_id=clip_id,
        campaign_id="test_camp",
        approved=True,
        final_score=8.5,
        hook_score=9.0,
        cta_score=8.0,
        viral_score=8.5,
        density_score=7.5,
        hard_failures=[],
        soft_warnings=[],
        rule_results={"test": True},
    )

    saved = store.create_campaign_evaluation(row)
    assert saved.clip_id == clip_id

    # Verify foreign_key_check passes with 0 violations
    with connection() as conn:
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert len(fk_violations) == 0


def test_create_campaign_evaluation_fk_failure_on_nonexistent_parent(initialised_db):
    """Verify SQLite foreign key enforcement triggers IntegrityError when parent clip does not exist."""
    non_existent_clip_id = "non_existent_clip_123"
    row = CampaignEvaluationRow(
        clip_id=non_existent_clip_id,
        campaign_id="test_camp",
        approved=True,
        final_score=8.5,
    )
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        store.create_campaign_evaluation(row)
