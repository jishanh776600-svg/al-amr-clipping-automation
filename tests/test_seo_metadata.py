"""Comprehensive focused automated tests for Step 23 Campaign-Aware SEO & Metadata Engine."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from autoclip.campaign.models_intelligence import (
    CampaignSpecification,
    RequirementItem,
)
from autoclip.db import models, schema, store
from autoclip.db.models import (
    Clip,
    ClipCandidateRecord,
    ClipMetadataRecord,
    Export,
    Job,
    Source,
)
from autoclip.seo import (
    CampaignSEORequirements,
    ComplianceResult,
    ComplianceStatus,
    MetadataQualityGate,
    SEOEngine,
    extract_campaign_seo_requirements,
)


def test_campaign_seo_extraction():
    """Extracts normalized requirements from CampaignSpecification accurately."""
    spec = CampaignSpecification(
        campaign_id="camp_test_001",
        title="Al Amr Masterclass",
        keywords=[RequirementItem(value="Artificial Intelligence"), RequirementItem(value="Future of Work")],
        required_mentions=[RequirementItem(value="alamr_official"), RequirementItem(value="@founder")],
        hashtags=[RequirementItem(value="#Innovation"), RequirementItem(value="Tech2026")],
        banned_words=[RequirementItem(value="guaranteed profits"), RequirementItem(value="crypto")],
        banned_topics=[RequirementItem(value="politics")],
        cta_required=RequirementItem(value=True),
        cta_instructions=[RequirementItem(value="👉 Click link in bio to enroll now!")],
        campaign_url="https://alamr.ai/masterclass",
    )

    reqs = extract_campaign_seo_requirements(spec)
    assert reqs.campaign_id == "camp_test_001"
    assert "Artificial Intelligence" in reqs.required_phrases
    assert "@alamr_official" in reqs.required_mentions
    assert "@founder" in reqs.required_mentions
    assert "#Innovation" in reqs.required_hashtags
    assert "#Tech2026" in reqs.required_hashtags
    assert "guaranteed profits" in reqs.prohibited_terms
    assert "crypto" in reqs.prohibited_terms
    assert reqs.cta_required is True
    assert reqs.campaign_url == "https://alamr.ai/masterclass"


def test_quality_gate_prohibited_terms_rejection():
    """Prohibited terms trigger hard violation and SEO_REJECT."""
    reqs = CampaignSEORequirements(
        prohibited_terms=["scam", "crypto pump"],
    )
    gate = MetadataQualityGate(reqs)

    # 1. Prohibited term in title
    res1 = gate.evaluate(
        title="Why this is not a crypto pump scheme",
        description="A great talk on scaling.",
        hashtags=["#Business"],
        mentions=[],
    )
    assert res1.status == ComplianceStatus.SEO_REJECT
    assert res1.score == 65.0 or res1.score < 80.0
    assert any("crypto pump" in err for err in res1.errors)

    # 2. Prohibited term in description
    res2 = gate.evaluate(
        title="Scaling Your Enterprise",
        description="Don't fall for any scam in business.",
        hashtags=["#Tech"],
        mentions=[],
    )
    assert res2.status == ComplianceStatus.SEO_REJECT
    assert any("scam" in err for err in res2.errors)


def test_quality_gate_mandatory_campaign_requirements():
    """Mandatory phrases, mentions, hashtags, and CTA must be present."""
    reqs = CampaignSEORequirements(
        required_phrases=["Generative AI"],
        required_mentions=["@alamr"],
        required_hashtags=["#AlAmrGlobal"],
        cta_required=True,
        campaign_url="https://alamr.ai",
    )
    gate = MetadataQualityGate(reqs)

    # Fails when requirements are missing
    res_fail = gate.evaluate(
        title="A General Discussion on Technology",
        description="We talked about algorithms and systems.",
        hashtags=["#Tech", "#Code"],
        mentions=["@someone"],
        cta="",
    )
    assert res_fail.status == ComplianceStatus.SEO_REJECT
    assert any("Generative AI" in err for err in res_fail.errors)
    assert any("@alamr" in err for err in res_fail.errors)
    assert any("#AlAmrGlobal" in err for err in res_fail.errors)
    assert any("Call To Action" in err for err in res_fail.errors)
    assert any("https://alamr.ai" in err for err in res_fail.errors)

    # Passes when all requirements are satisfied
    res_pass = gate.evaluate(
        title="The Future of Generative AI",
        description="Deep dive into Generative AI systems.\n\nLearn more at https://alamr.ai\nFeaturing @alamr",
        hashtags=["#AlAmrGlobal", "#Tech", "#Shorts"],
        mentions=["@alamr"],
        cta="Follow for more daily AI breakthroughs!",
    )
    assert res_pass.status == ComplianceStatus.SEO_PASS
    assert res_pass.score == 100.0
    assert res_pass.is_publish_ready is True


def test_quality_gate_title_and_description_constraints():
    """Title length constraints (<= 100 chars) and empty fields are enforced."""
    gate = MetadataQualityGate(CampaignSEORequirements())

    # Empty title
    r_empty_title = gate.evaluate(title="", description="Valid desc", hashtags=[], mentions=[])
    assert r_empty_title.status == ComplianceStatus.SEO_REJECT
    assert any("Title cannot be empty" in err for err in r_empty_title.errors)

    # Too long title (> 100 chars)
    long_title = "A" * 105
    r_long_title = gate.evaluate(title=long_title, description="Valid desc", hashtags=[], mentions=[])
    assert r_long_title.status == ComplianceStatus.SEO_REJECT
    assert any("exceeds maximum length" in err for err in r_long_title.errors)

    # Malformed unsafe link
    r_bad_link = gate.evaluate(title="Valid Title", description="Check this out: http://bad<link>", hashtags=[], mentions=[])
    assert r_bad_link.status == ComplianceStatus.SEO_REJECT
    assert any("unsafe link" in err for err in r_bad_link.errors)


def test_clip_specific_metadata_generation(initialised_db):
    """Two clips from the same source generate distinct, clip-specific metadata."""
    store.create_source(Source(id="src-seo-1", type="upload", path="test.mp4"))
    store.create_job(Job(id="job-seo-1", source_id="src-seo-1", status="running"))

    clip1 = Clip(
        id="c1",
        job_id="job-seo-1",
        rank=1,
        start_s=0.0,
        end_s=15.0,
        title="The Zero to One Moment",
        hook="Starting a company is like jumping off a cliff.",
        score=92,
    )
    clip2 = Clip(
        id="c2",
        job_id="job-seo-1",
        rank=2,
        start_s=30.0,
        end_s=45.0,
        title="Hiring A-Players Only",
        hook="Never compromise on engineering talent.",
        score=88,
    )
    store.create_clip(clip1)
    store.create_clip(clip2)

    spec = CampaignSpecification(
        campaign_id="camp-1",
        title="Startup Insights",
        required_mentions=[RequirementItem(value="@alamr")],
        hashtags=[RequirementItem(value="#Startups")],
    )

    engine = SEOEngine.from_campaign_spec(spec)
    meta1 = engine.generate_for_clip(clip1, transcript_text="Jumping off a cliff and assembling an airplane on the way down.")
    meta2 = engine.generate_for_clip(clip2, transcript_text="When hiring engineers, look for deep curiosity and grit.")

    assert meta1.clip_id == "c1"
    assert meta2.clip_id == "c2"
    assert meta1.generated_title != meta2.generated_title
    assert meta1.generated_description != meta2.generated_description
    assert "cliff" in meta1.generated_title.lower() or "zero to one" in meta1.generated_title.lower()
    assert "talent" in meta2.generated_title.lower() or "hiring" in meta2.generated_title.lower()
    assert "#Startups" in meta1.generated_hashtags
    assert "#Startups" in meta2.generated_hashtags
    assert "@alamr" in meta1.generated_mentions


def test_operator_edit_persistence_and_versioning(initialised_db):
    """Operator edits update final_* without overwriting generated_* and increment version."""
    store.create_source(Source(id="src-seo-2", type="upload", path="test.mp4"))
    store.create_job(Job(id="job-seo-2", source_id="src-seo-2", status="running"))
    clip = Clip(id="c-edit", job_id="job-seo-2", rank=1, start_s=0.0, end_s=10.0, title="Initial Title", hook="Hook text")
    store.create_clip(clip)

    engine = SEOEngine.from_campaign_spec(None)
    initial_rec = engine.generate_for_clip(clip, transcript_text="Some transcript content.")
    assert initial_rec.version == 1
    assert initial_rec.final_title == initial_rec.generated_title

    orig_generated_title = initial_rec.generated_title

    # Operator edits metadata
    updated = engine.update_operator_metadata(
        clip_id="c-edit",
        final_title="Custom Operator Refined Title",
        final_description="Operator customized description.",
        final_hashtags=["#CustomTag", "#Viral"],
        final_mentions=["@customoperator"],
        final_cta="Check out the link below!",
    )

    assert updated.version == 2
    assert updated.generated_title == orig_generated_title
    assert updated.final_title == "Custom Operator Refined Title"
    assert updated.final_description == "Operator customized description."
    assert updated.final_hashtags == ["#CustomTag", "#Viral"]
    assert updated.final_mentions == ["@customoperator"]
    assert len(updated.telemetry.get("history", [])) == 1

    # Verify persistent retrieval from DB
    persisted = store.get_clip_metadata("c-edit")
    assert persisted is not None
    assert persisted.version == 2
    assert persisted.final_title == "Custom Operator Refined Title"
    assert persisted.generated_title == orig_generated_title


def test_reset_to_generated(initialised_db):
    """Operator can reset metadata back to generated draft."""
    store.create_source(Source(id="src-seo-3", type="upload", path="test.mp4"))
    store.create_job(Job(id="job-seo-3", source_id="src-seo-3", status="running"))
    clip = Clip(id="c-reset", job_id="job-seo-3", rank=1, start_s=0.0, end_s=10.0, title="Reset Test", hook="Hook text")
    store.create_clip(clip)

    engine = SEOEngine.from_campaign_spec(None)
    initial_rec = engine.generate_for_clip(clip, transcript_text="Transcript words here.")

    # Apply operator edit
    engine.update_operator_metadata(clip_id="c-reset", final_title="Temporary Edit")
    edited = store.get_clip_metadata("c-reset")
    assert edited.final_title == "Temporary Edit"
    assert edited.version == 2

    # Reset to generated
    reset_rec = engine.update_operator_metadata(clip_id="c-reset", reset_to_generated=True)
    assert reset_rec.final_title == reset_rec.generated_title
    assert reset_rec.version == 3


def test_idempotent_regeneration_preserves_operator_edits(initialised_db):
    """Re-running generation for a clip does not overwrite operator edits."""
    store.create_source(Source(id="src-seo-4", type="upload", path="test.mp4"))
    store.create_job(Job(id="job-seo-4", source_id="src-seo-4", status="running"))
    clip = Clip(id="c-idem", job_id="job-seo-4", rank=1, start_s=0.0, end_s=10.0, title="Idem Test", hook="Hook text")
    store.create_clip(clip)

    engine = SEOEngine.from_campaign_spec(None)
    engine.generate_for_clip(clip, transcript_text="Some text.")

    # Edit
    engine.update_operator_metadata(clip_id="c-idem", final_title="Operator Handcrafted Title")

    # Re-run generate_for_clip
    re_gen = engine.generate_for_clip(clip, transcript_text="Different text completely.")
    assert re_gen.final_title == "Operator Handcrafted Title"
    assert re_gen.version >= 2


@pytest.mark.asyncio
async def test_publishing_blocked_for_seo_reject(initialised_db):
    """Publishing is rejected with HTTP 400 if metadata is in SEO_REJECT state."""
    from autoclip.api.publishing import publish_export_endpoint
    from autoclip.api.schemas import PublishRequestIn

    store.create_source(Source(id="src-pub-seo", type="upload", path="p"))
    store.create_job(Job(id="job-pub-seo", source_id="src-pub-seo", status="running"))
    store.create_clip(Clip(id="clip-pub-seo", job_id="job-pub-seo", rank=1, start_s=0.0, end_s=5.0, status="exported", score=85))

    export_rec = store.create_export(
        Export(
            id="exp-seo-1",
            clip_id="clip-pub-seo",
            path="/tmp/final.mp4",
            ratio="9:16",
            style="classic_professional",
            size_bytes=5000,
        )
    )

    # Valid final render
    from autoclip.db.models import FinalRenderRecord
    store.replace_final_renders(
        "job-pub-seo",
        [
            FinalRenderRecord(
                id="render-pub-seo",
                job_id="job-pub-seo",
                clip_id="clip-pub-seo",
                output_path="/tmp/final.mp4",
                package_dir="/tmp",
                quality_score=100.0,
                quality_status="RENDER_PASS",
                render_status="completed",
            )
        ],
    )

    # Invalid SEO metadata with compliance violation (empty title + prohibited term)
    store.create_clip_metadata(
        ClipMetadataRecord(
            id="meta-fail",
            job_id="job-pub-seo",
            clip_id="clip-pub-seo",
            generated_title="",
            final_title="",
            generated_description="Valid description",
            final_description="Valid description",
            compliance_status="SEO_REJECT",
            compliance_score=0.0,
            validation_errors=["Title cannot be empty."],
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await publish_export_endpoint(
            export_id="exp-seo-1",
            request=PublishRequestIn(platforms=["telegram"]),
        )

    assert exc_info.value.status_code == 400
    assert "SEO metadata failed compliance check" in str(exc_info.value.detail)


def test_db_store_clip_metadata_crud(initialised_db):
    """Verifies SQLite schema V18 migration and CRUD operations for ClipMetadataRecord."""
    store.create_source(Source(id="src-v18", type="upload", path="p"))
    store.create_job(Job(id="job-v18", source_id="src-v18", status="running"))
    store.create_clip(Clip(id="clip-v18", job_id="job-v18", rank=1, start_s=0.0, end_s=5.0, status="candidate"))

    record = ClipMetadataRecord(
        id="meta-v18-1",
        job_id="job-v18",
        clip_id="clip-v18",
        generated_title="AI in 2026",
        final_title="AI in 2026: The Masterclass",
        generated_description="Draft description",
        final_description="Final approved description",
        generated_hashtags=["#AI"],
        final_hashtags=["#AI", "#Future"],
        generated_mentions=["@alamr"],
        final_mentions=["@alamr", "@tech"],
        generated_cta="Follow us",
        final_cta="Follow us for more updates!",
        compliance_status="SEO_PASS",
        compliance_score=100.0,
        version=1,
    )

    created = store.create_clip_metadata(record)
    assert created.id == "meta-v18-1"

    fetched = store.get_clip_metadata("clip-v18")
    assert fetched is not None
    assert fetched.final_title == "AI in 2026: The Masterclass"
    assert fetched.final_hashtags == ["#AI", "#Future"]
    assert fetched.is_publish_ready is True

    listed = store.list_clip_metadata_for_job("job-v18")
    assert len(listed) == 1
    assert listed[0].id == "meta-v18-1"


def test_api_metadata_endpoints(initialised_db):
    """Verifies GET /api/jobs/{job_id}/metadata and PATCH /api/jobs/{job_id}/clips/{clip_id}/metadata."""
    from autoclip.app import create_app
    client = TestClient(create_app())

    source = store.create_source(Source(id=models.new_id(), type="upload", path="test.mp4"))
    job = store.create_job(Job(id=models.new_id(), source_id=source.id, status="done"))
    clip = store.create_clip(Clip(id=models.new_id(), job_id=job.id, rank=1, start_s=0.0, end_s=10.0, title="API Clip"))

    engine = SEOEngine.from_campaign_spec(None)
    meta = engine.generate_for_clip(clip, transcript_text="API testing transcript.")

    # 1. GET /api/jobs/{job_id}/metadata
    res_list = client.get(f"/api/jobs/{job.id}/metadata")
    assert res_list.status_code == 200
    data_list = res_list.json()
    assert len(data_list) == 1
    assert data_list[0]["clip_id"] == clip.id
    assert data_list[0]["version"] == 1

    # 2. PATCH /api/jobs/{job_id}/clips/{clip_id}/metadata
    patch_payload = {
        "final_title": "Patched Final Title",
        "final_description": "Patched Final Description",
        "final_hashtags": ["#API", "#Test"],
        "final_mentions": ["@alamr_dev"],
        "final_cta": "Subscribe now!",
    }
    res_patch = client.patch(f"/api/jobs/{job.id}/clips/{clip.id}/metadata", json=patch_payload)
    assert res_patch.status_code == 200
    patched_data = res_patch.json()
    assert patched_data["final_title"] == "Patched Final Title"
    assert patched_data["final_description"] == "Patched Final Description"
    assert patched_data["final_hashtags"] == ["#API", "#Test"]
    assert patched_data["version"] == 2
    assert patched_data["is_publish_ready"] is True

    # 3. PATCH reset_to_generated
    res_reset = client.patch(f"/api/jobs/{job.id}/clips/{clip.id}/metadata", json={"action": "reset_to_generated"})
    assert res_reset.status_code == 200
    reset_data = res_reset.json()
    assert reset_data["final_title"] == meta.generated_title
    assert reset_data["version"] == 3

