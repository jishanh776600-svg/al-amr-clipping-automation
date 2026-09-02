"""Unit tests for publishing models, metadata builders, and text sanitization."""

from datetime import datetime, timezone
import pytest
from clipping.publishing.models import (
    PublishStatus,
    PrivacyStatus,
    FailureClassification,
    YouTubeVideoMetadata,
    PublishRequest,
    PublishSummary,
)
from clipping.publishing.metadata import YouTubeMetadataBuilder, sanitize_youtube_text


def test_sanitize_youtube_text():
    raw = "Exclusive Interview <Must Watch>   Breaking News"
    cleaned = sanitize_youtube_text(raw)
    assert "<" not in cleaned
    assert ">" not in cleaned
    assert "Exclusive Interview Must Watch Breaking News" == cleaned


def test_metadata_builder_shorts_tagging():
    meta = YouTubeMetadataBuilder.build(
        title="Epic Coding Revelation",
        description="Here is why automated clipping changes everything.",
        tags=["Python", "AI"],
        privacy_status=PrivacyStatus.UNLISTED,
    )
    assert "#Shorts" in meta.title
    assert len(meta.title) <= 100
    assert "#Shorts" in meta.description
    assert "Python" in meta.tags
    assert "Shorts" in meta.tags
    assert meta.privacy_status == PrivacyStatus.UNLISTED


def test_metadata_builder_title_length_limit():
    long_title = "A" * 150
    meta = YouTubeMetadataBuilder.build(title=long_title)
    assert len(meta.title) <= 100
    assert meta.title.endswith("#Shorts")


def test_publish_request_model():
    meta = YouTubeMetadataBuilder.build(title="Test Short")
    req = PublishRequest(
        job_id="job_001",
        clip_id="clip_001",
        approval_request_id="req_001",
        idempotency_key="job_001:clip_001:v1",
        video_storage_key="clips/clip_001/final_1080x1920.mp4",
        metadata=meta,
        status=PublishStatus.READY,
    )
    assert req.job_id == "job_001"
    assert req.status == PublishStatus.READY
    assert req.attempt_count == 0


def test_publish_summary_metrics():
    summary = PublishSummary(
        job_id="job_123",
        total_clips=4,
        published_count=2,
        skipped_count=1,
        deferred_count=1,
        failed_count=0,
        all_processed=True,
    )
    assert summary.all_processed is True
    assert summary.published_count == 2
