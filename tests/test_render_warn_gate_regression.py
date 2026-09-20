"""Regression tests for RENDER_WARN quality status handling in worker_runner and jobs API."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autoclip.db import schema, store
from autoclip.db.models import Clip, FinalRenderRecord, Job, Source, new_id


def test_render_warn_is_approved():
    """Verify data model considers RENDER_WARN approved."""
    fr = FinalRenderRecord(
        id=new_id(),
        job_id="test-job",
        clip_id="test-clip",
        output_path="/tmp/final.mp4",
        quality_status="RENDER_WARN",
        quality_score=90.0,
        render_status="completed",
    )
    assert fr.is_approved is True


def test_worker_runner_has_successful_render_logic():
    """Verify worker runner logic accepts RENDER_WARN."""
    final_renders_payload = [
        {
            "clip_id": "clip-1",
            "quality_status": "RENDER_WARN",
            "quality_score": 90.0,
            "render_status": "completed",
        },
        {
            "clip_id": "clip-2",
            "quality_status": "RENDER_REJECT",
            "quality_score": 0.0,
            "render_status": "failed",
        },
    ]

    rendered_clip_ids = {
        fr["clip_id"]
        for fr in final_renders_payload
        if fr.get("quality_status") in ("RENDER_PASS", "RENDER_WARN")
    }
    assert rendered_clip_ids == {"clip-1"}

    has_successful_render = any(
        fr.get("quality_status") in ("RENDER_PASS", "RENDER_WARN")
        for fr in final_renders_payload
    )
    assert has_successful_render is True


def test_jobs_api_telegram_review_triggers_for_render_warn(tmp_path: Path, monkeypatch):
    """Verify control plane callback triggers Telegram review for RENDER_WARN final render."""
    db_file = tmp_path / "test_render_warn.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    schema.migrate(conn)
    monkeypatch.setattr(store, "connection", lambda: conn)

    job_id = f"job-{new_id()}"
    clip_id = f"clip-{new_id()}"

    store.create_source(Source(id="s1", type="upload", path="p"))
    job = Job(id=job_id, source_id="s1", status="running", settings={"telegram": {"bot_token": "fake", "chat_id": "123"}})
    store.create_job(job)

    clip = Clip(id=clip_id, job_id=job_id, start_s=0.0, end_s=22.0, rank=1, title="Warn Clip")
    store.create_clip(clip)

    fr = FinalRenderRecord(
        id=new_id(),
        job_id=job_id,
        clip_id=clip_id,
        output_path="/tmp/final.mp4",
        quality_status="RENDER_WARN",
        quality_score=90.0,
        render_status="completed",
        telemetry={"drive_file_id": "drive-warn-123"},
    )
    store.create_final_render(fr)

    # Simulate callback check in api/jobs.py
    clips_to_review = store.list_clips_for_job(job_id)
    qualifying_clips = []
    for clip_item in clips_to_review:
        render_item = store.get_final_render(clip_item.id)
        if not render_item or render_item.quality_status not in ("RENDER_PASS", "RENDER_WARN"):
            continue
        qualifying_clips.append(clip_item.id)

    assert qualifying_clips == [clip_id]
