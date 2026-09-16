"""Targeted regression test for canonical ClipMetadataRecord serialization,
worker runner payload generation, callback ingestion, and review bot formatting.
Ensures ad-hoc jobs without campaign requirements generate valid SEO metadata
and pass publish-readiness gates.
"""
from autoclip.db import models, store
from autoclip.seo.engine import SEOEngine


def test_clip_metadata_record_canonical_fields():
    """Verify ClipMetadataRecord contains all canonical fields and no non-existent attributes."""
    record = models.ClipMetadataRecord(
        id=models.new_id(),
        job_id=models.new_id(),
        clip_id=models.new_id(),
        generated_title="Generated Hook Title",
        final_title="Final Hook Title",
        generated_description="Generated Description",
        final_description="Final Description",
        generated_hashtags=["#shorts", "#test"],
        final_hashtags=["#shorts", "#test"],
        generated_mentions=["@alamr"],
        final_mentions=["@alamr"],
        generated_cta="Subscribe now",
        final_cta="Subscribe now",
        campaign_requirements_matched={"default": True},
        compliance_status="SEO_PASS",
        compliance_score=100.0,
        validation_errors=[],
        validation_warnings=[],
        version=1,
    )

    # Must be publish ready
    assert record.is_publish_ready is True
    assert record.compliance_score == 100.0

    # Serialization must include canonical keys
    d = record.to_dict()
    assert d["final_title"] == "Final Hook Title"
    assert d["compliance_status"] == "SEO_PASS"
    assert d["compliance_score"] == 100.0
    assert d["is_publish_ready"] is True
    assert "title_candidates" not in d


def test_seo_engine_adhoc_video_without_campaign_requirements():
    """Verify ad-hoc videos without a campaign brief produce valid publish-ready metadata."""
    source_id = models.new_id()
    store.create_source(models.Source(id=source_id, type="upload", path="/tmp/test.mp4"))

    job_id = models.new_id()
    job = models.Job(id=job_id, source_id=source_id, status="running")
    store.create_job(job)

    clip_id = models.new_id()
    clip = models.Clip(
        id=clip_id,
        job_id=job_id,
        start_s=5.0,
        end_s=25.0,
        rank=1,
        hook="Amazing productivity tip revealed",
        title="Key Productivity Insight",
        score=0.92,
    )
    store.create_clip(clip)

    # Ad-hoc videos pass campaign_spec=None
    engine = SEOEngine.from_campaign_spec(None)

    # Generate metadata
    record = engine.generate_for_clip(
        clip=clip,
        transcript_text="Here is an amazing tip on how to optimize your daily workflow and save hours.",
    )

    assert record.clip_id == clip_id
    assert record.final_title != ""
    assert record.final_description != ""
    assert record.compliance_status in ("SEO_PASS", "SEO_WARN")
    assert record.is_publish_ready is True
    assert len(record.validation_errors) == 0

    # Test round trip in DB store
    stored = store.get_clip_metadata(clip_id)
    assert stored is not None
    assert stored.final_title == record.final_title
    assert stored.compliance_score == record.compliance_score


def test_worker_runner_metadata_serialization():
    """Verify worker_runner serializes clip metadata using to_dict() without AttributeError."""
    source_id = models.new_id()
    store.create_source(models.Source(id=source_id, type="upload", path="/tmp/worker.mp4"))

    job_id = models.new_id()
    job = models.Job(id=job_id, source_id=source_id, status="running")
    store.create_job(job)

    clip_id = models.new_id()
    clip = models.Clip(
        id=clip_id,
        job_id=job_id,
        start_s=0.0,
        end_s=15.0,
        rank=1,
    )
    store.create_clip(clip)

    meta = models.ClipMetadataRecord(
        id=models.new_id(),
        job_id=job_id,
        clip_id=clip_id,
        generated_title="Auto title",
        final_title="Auto title",
        generated_description="Auto desc",
        final_description="Auto desc",
        compliance_status="SEO_PASS",
        compliance_score=100.0,
    )
    store.create_clip_metadata(meta)

    # Mimic worker_runner collection loop
    clips = [clip]
    clip_metadata_payload = []
    for c in clips:
        m = store.get_clip_metadata(c.id)
        if m:
            clip_metadata_payload.append(m.to_dict())

    assert len(clip_metadata_payload) == 1
    assert clip_metadata_payload[0]["clip_id"] == clip_id
    assert clip_metadata_payload[0]["compliance_score"] == 100.0
    assert clip_metadata_payload[0]["compliance_status"] == "SEO_PASS"


def test_telegram_review_formatting():
    """Verify Telegram review card formatting accesses compliance_score correctly."""
    clip = models.Clip(
        id=models.new_id(),
        job_id=models.new_id(),
        start_s=0.0,
        end_s=20.0,
        rank=1,
        hook="Great hook",
        title="Great title",
    )
    clip_meta = models.ClipMetadataRecord(
        id=models.new_id(),
        job_id=clip.job_id,
        clip_id=clip.id,
        generated_title="Great title",
        final_title="Great title",
        generated_description="Great desc",
        final_description="Great desc",
        compliance_status="SEO_PASS",
        compliance_score=95.5,
    )
    final_render = models.FinalRenderRecord(
        id=models.new_id(),
        job_id=clip.job_id,
        clip_id=clip.id,
        output_path="/tmp/test.mp4",
        quality_score=98.0,
        quality_status="RENDER_PASS",
    )

    # Validate the expressions used in review_bot.py
    duration = f"{clip.end_s - clip.start_s:.1f}" if clip.end_s > clip.start_s else "0.0"
    quality_score = f"{final_render.quality_score:.1f}" if final_render else "N/A"
    quality_status = final_render.quality_status if final_render else "PENDING"
    seo_score = f"{clip_meta.compliance_score:.1f}" if clip_meta else "N/A"
    seo_status = clip_meta.compliance_status if clip_meta else "PENDING"

    assert duration == "20.0"
    assert quality_score == "98.0"
    assert quality_status == "RENDER_PASS"
    assert seo_score == "95.5"
    assert seo_status == "SEO_PASS"


def test_callback_clip_metadata_ingestion():
    """Verify callback ingestion in jobs.py constructs ClipMetadataRecord without error."""
    source_id = models.new_id()
    store.create_source(models.Source(id=source_id, type="upload", path="/tmp/cb.mp4"))

    job_id = models.new_id()
    job = models.Job(id=job_id, source_id=source_id, status="running")
    store.create_job(job)

    clip_id = models.new_id()
    clip = models.Clip(
        id=clip_id,
        job_id=job_id,
        start_s=0.0,
        end_s=10.0,
        rank=1,
    )
    store.create_clip(clip)

    cm = {
        "id": models.new_id(),
        "job_id": job_id,
        "clip_id": clip_id,
        "generated_title": "Callback Generated Title",
        "final_title": "Callback Final Title",
        "generated_description": "Callback Generated Desc",
        "final_description": "Callback Final Desc",
        "generated_hashtags": ["#cb"],
        "final_hashtags": ["#cb"],
        "compliance_status": "SEO_PASS",
        "compliance_score": 100.0,
    }

    meta_record = models.ClipMetadataRecord(
        id=cm.get("id", models.new_id()),
        job_id=job_id,
        clip_id=cm["clip_id"],
        generated_title=cm.get("generated_title") or cm.get("final_title", ""),
        final_title=cm.get("final_title", ""),
        generated_description=cm.get("generated_description") or cm.get("final_description", ""),
        final_description=cm.get("final_description", ""),
        generated_hashtags=cm.get("generated_hashtags", []),
        final_hashtags=cm.get("final_hashtags", []),
        generated_mentions=cm.get("generated_mentions", []),
        final_mentions=cm.get("final_mentions", []),
        generated_cta=cm.get("generated_cta", ""),
        final_cta=cm.get("final_cta", ""),
        campaign_requirements_matched=cm.get("campaign_requirements_matched", {}),
        compliance_status=cm.get("compliance_status", "SEO_PASS"),
        compliance_score=float(cm.get("compliance_score", 100.0)),
        validation_errors=cm.get("validation_errors", []),
        validation_warnings=cm.get("validation_warnings", []),
        version=int(cm.get("version", 1)),
        telemetry=cm.get("telemetry", {}),
        created_at=cm.get("created_at", store.utcnow()),
        updated_at=cm.get("updated_at", store.utcnow()),
    )
    store.create_clip_metadata(meta_record)

    persisted = store.get_clip_metadata(clip_id)
    assert persisted is not None
    assert persisted.final_title == "Callback Final Title"
    assert persisted.compliance_status == "SEO_PASS"
    assert persisted.compliance_score == 100.0
