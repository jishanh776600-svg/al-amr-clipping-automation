"""Tests for AL AMR Campaign Guideline Document Ingestion and Autonomous Execution."""

from __future__ import annotations

import io
import os
from pathlib import Path

from collections.abc import Iterator
from unittest.mock import patch

import docx
import pypdf
import pytest
from fastapi.testclient import TestClient

from autoclip import app as app_module
from autoclip.app import create_app
from autoclip.campaign.extractor import (
    GuidelineExtractionError,
    extract_guideline_text,
    extract_text_from_docx,
    extract_text_from_pdf,
    parse_guidelines_into_brief,
    validate_guideline_file,
)
from autoclip.campaign.models import CampaignBrief
from autoclip.db import store
from autoclip.db.models import CampaignGuideline, Source, new_id
from autoclip.providers import build_provider
from autoclip.providers.autonomous_provider import AutonomousProvider
from autoclip.providers.base import DetectionConfig, TranscriptWindow


@pytest.fixture
def client(autoclip_home, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    with TestClient(create_app()) as test_client:
        yield test_client


def _make_sample_docx(
    title: str = "Summer Launch Campaign",
    audience: str = "Software Engineers & AI Researchers",
    topics: list[str] | None = None,
    duration: str = "30-60s",
    tone: str = "educational",
    cta: str = "Subscribe for future updates!",
) -> bytes:
    doc = docx.Document()
    doc.add_heading(title, level=0)
    doc.add_paragraph(f"Target Audience: {audience}")
    topics_list = topics or ["AI Automation", "Face Tracking", "Active Speaker Framing"]
    doc.add_paragraph("Key Focus Areas:")
    for t in topics_list:
        doc.add_paragraph(f"- {t}")
    doc.add_paragraph(f"Target Duration: {duration}")
    doc.add_paragraph(f"Tone: {tone}")
    doc.add_paragraph(f"Call to Action: {cta}")

    # Add sample table
    table = doc.add_table(rows=1, cols=2)
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "Platform"
    hdr_cells[1].text = "Ratio"
    row_cells = table.add_row().cells
    row_cells[0].text = "YouTube Shorts"
    row_cells[1].text = "9:16"

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_sample_pdf(text: str = "AI Video Campaign\nTarget Audience: Tech Creators\nTopics: Autonomous Clipping") -> bytes:
    # Minimal valid single-page PDF with text stream
    stream_content = f"BT\n/F1 12 Tf\n72 712 Td\n({text}) Tj\nET\n".encode("latin1")
    length = len(stream_content)
    pdf_bytes = b"".join([
        b"%PDF-1.4\n",
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n",
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<\n/Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n",
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n",
        f"5 0 obj<</Length {length}>>stream\n".encode("ascii"),
        stream_content,
        b"endstream\nendobj\n",
        b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n",
        b"0000000108 00000 n \n0000000213 00000 n \n0000000279 00000 n \n",
        b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n443\n%%EOF",
    ])
    return pdf_bytes


def test_validate_guideline_file_types():
    # Empty file
    with pytest.raises(GuidelineExtractionError, match="completely empty"):
        validate_guideline_file("test.pdf", b"")

    # Unsupported format
    with pytest.raises(GuidelineExtractionError, match="Unsupported file format"):
        validate_guideline_file("notes.txt", b"Hello world")

    # Fake PDF without %PDF- magic bytes
    with pytest.raises(GuidelineExtractionError, match="valid PDF document"):
        validate_guideline_file("corrupt.pdf", b"NOT A REAL PDF")

    # Fake DOCX without PK\x03\x04 zip header
    with pytest.raises(GuidelineExtractionError, match="valid Word document"):
        validate_guideline_file("corrupt.docx", b"NOT A ZIP CONTAINER")


def test_extract_docx_ordered_text_and_brief():
    raw_docx = _make_sample_docx(
        title="Production Q3 Guidelines",
        audience="Mobile Video Editors",
        topics=["Smart Framing", "Kinetic Subtitles", "Fast Whisper"],
        duration="25-50 seconds",
        tone="energetic",
        cta="Subscribe for more tutorials!",
    )
    text = extract_text_from_docx(raw_docx)
    assert "Production Q3 Guidelines" in text
    assert "Smart Framing" in text
    assert "YouTube Shorts | 9:16" in text

    brief = parse_guidelines_into_brief(text, "production_q3.docx")
    assert isinstance(brief, CampaignBrief)
    assert "Production Q3" in brief.name
    assert brief.target_audience == "Mobile Video Editors"
    assert any("Smart Framing" in t for t in brief.required_topics)
    assert brief.minimum_duration == 25.0
    assert brief.maximum_duration == 50.0
    assert brief.cta_required is True
    assert "subscribe" in brief.cta_types


def test_extract_pdf_ordered_text_and_brief():
    pdf_bytes = _make_sample_pdf("Enterprise AI Workflow\nTarget Audience: Enterprise Leaders")
    text = extract_text_from_pdf(pdf_bytes)
    assert "Enterprise AI Workflow" in text

    brief = parse_guidelines_into_brief(text, "enterprise_guide.pdf")
    assert "Enterprise AI Workflow" in brief.name
    assert brief.target_audience == "Enterprise Leaders"


def test_blank_pdf_rejection():
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    blank_bytes = buf.getvalue()

    with pytest.raises(GuidelineExtractionError, match="no readable text"):
        extract_text_from_pdf(blank_bytes)


def test_guideline_api_upload_endpoint(client):
    raw_docx = _make_sample_docx(title="API Test Brief")
    res = client.post(
        "/api/jobs/guidelines/upload",
        files={"file": ("api_test.docx", raw_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert res.status_code == 201
    data = res.json()
    assert "id" in data
    assert data["filename"] == "api_test.docx"
    assert data["status"] == "extracted"
    assert data["parsed_brief"]["name"] == "API Test Brief"

    # Verify persisted in database
    guideline = store.get_guideline(data["id"])
    assert guideline is not None
    assert guideline.filename == "api_test.docx"


def test_create_job_with_guideline_and_manifest(client):
    # 1. Create a source
    source = Source(
        id=new_id(),
        type="upload",
        path="dummy_source.mp4",
        title="Test Campaign Source Video",
        duration_s=120.0,
    )
    store.create_source(source)

    # 2. Upload a guideline document
    raw_docx = _make_sample_docx(
        title="Autonomous Campaign Alpha",
        topics=["Autonomous AI", "Video Automation"],
    )
    upload_res = client.post(
        "/api/jobs/guidelines/upload",
        files={"file": ("alpha_guidelines.docx", raw_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert upload_res.status_code == 201
    guideline_id = upload_res.json()["id"]

    # 3. Create job linking guideline_id
    job_res = client.post(
        "/api/jobs",
        json={"source_id": source.id, "guideline_id": guideline_id},
    )
    assert job_res.status_code == 201
    job_data = job_res.json()
    job_id = job_data["id"]
    assert job_data.get("guideline") is not None
    assert job_data["guideline"]["filename"] == "alpha_guidelines.docx"

    # 4. Verify authoritative manifest contains guideline metadata
    man_res = client.get(f"/api/jobs/{job_id}/manifest")
    assert man_res.status_code == 200
    manifest = man_res.json()
    assert manifest.get("guideline") is not None
    assert manifest["guideline"]["filename"] == "alpha_guidelines.docx"
    assert manifest.get("campaign") is not None
    assert manifest["campaign"]["name"] == "Autonomous Campaign Alpha"

    # 5. Verify guideline file download endpoint
    file_res = client.get(f"/api/jobs/{job_id}/guideline/file")
    assert file_res.status_code == 200
    assert len(file_res.content) == len(raw_docx)


def test_create_autonomous_job_multipart_endpoint(client):
    raw_docx = _make_sample_docx(title="Single Step Autonomous Flow")

    # Test error if no source is given
    err_res = client.post("/api/jobs/create-autonomous", data={})
    assert err_res.status_code == 400

    # Test with URL + guideline file
    mock_src = Source(
        id=new_id(),
        type="youtube",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        path="dummy.mp4",
        title="Rick Astley Video",
        duration_s=212.0,
    )
    with patch("autoclip.pipeline.ingest.ingest_url", return_value=mock_src):
        res = client.post(
            "/api/jobs/create-autonomous",
            data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            files={"guideline_file": ("flow_guide.docx", raw_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "queued"
        assert data["guideline"]["filename"] == "flow_guide.docx"
        assert data["guideline"]["parsed_brief"]["name"] == "Single Step Autonomous Flow"


def test_autonomous_provider_highlight_detection():
    provider = AutonomousProvider()
    assert provider.name == "autonomous"
    assert provider.requires_key is False

    import asyncio
    status = asyncio.run(provider.health_check())
    assert status.available is True
    assert "Autonomous" in status.detail

    # Create dummy transcript window
    words_text = " ".join(f"[{i}]word{i}" for i in range(50))
    window = TranscriptWindow(text=words_text, first_word=0, last_word=49)
    config = DetectionConfig(min_duration_s=5.0, max_duration_s=15.0, max_clips=3)

    candidates = asyncio.run(provider.detect_highlights(window, config))
    assert len(candidates.clips) > 0
    assert candidates.clips[0].score >= 70
    assert "Autonomous" in candidates.clips[0].reason


def test_provider_resolution_fallback_to_autonomous(fake_keyring):
    # build_provider with name=None falls back to autonomous when key is missing
    p = build_provider(None)
    assert p.name == "autonomous"

    # build_provider with autonomous explicitly
    p_auto = build_provider("autonomous")
    assert p_auto.name == "autonomous"
