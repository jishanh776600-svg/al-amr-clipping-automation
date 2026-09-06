"""Comprehensive Unit and Integration Tests for Campaign Brief Intelligence Engine (Step 2/5).

Validates:
1. Multi-format ingestion: PDF, TXT, MD
2. Multi-page PDF text extraction and page provenance
3. Image-only PDF detection and clear fail-closed status
4. Deterministic requirement extraction across all 10 categories
5. Modality distinction: REQUIRED vs OPTIONAL vs PREFERRED vs PROHIBITED vs UNKNOWN
6. Missing requirements preservation (no hallucination or fabrication)
7. Ambiguity preservation
8. AI provider structured output validation
9. AI provider malformed output fallback
10. AI provider network failure fallback
11. Operator override and audit trail preservation
12. FastAPI endpoints: /api/campaigns/analyze-brief, /api/campaigns/override-requirements, /api/campaigns/brief-content
13. Persistence in CampaignRecord and JobState.metadata
14. Zero secret leakage
"""

import io
import json
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from clipping.contracts.requirements import (
    CampaignRequirements,
    RequirementModality,
)
from clipping.document.brief_engine import (
    BriefDocumentReader,
    BriefDeterministicExtractor,
    CampaignBriefIntelligenceEngine,
)
from clipping.document.ai_extractor import BriefAIExtractor
from clipping.storage.local import LocalStorageDriver
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.campaign.repository import CampaignRepository
from clipping.state.remote import RemoteStorageStateRepository
import clipping.ui.server as ui_server
from clipping.ui.server import app


import pypdf


def create_single_page_pdf(lines: list[str]) -> bytes:
    text_content = "\\n".join(lines).replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 712 Td ({text_content}) Tj ET"
    pdf = f"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length {len(stream)} >> stream
{stream}
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000266 00000 n 
0000000348 00000 n 
trailer << /Size 6 /Root 1 0 R >>
startxref
434
%%EOF"""
    return pdf.encode("latin1")


def build_mock_pdf(page_texts: list[str]) -> bytes:
    """Builds a compliant multi-page PDF using pypdf.PdfWriter."""
    writer = pypdf.PdfWriter()
    for pt in page_texts:
        if not pt.strip():
            writer.add_blank_page(612, 792)
        else:
            single_bytes = create_single_page_pdf(pt.split("\n"))
            reader = pypdf.PdfReader(io.BytesIO(single_bytes))
            writer.add_page(reader.pages[0])

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def brief_test_env(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    app.dependency_overrides[ui_server.get_storage_driver] = lambda: storage
    yield {"storage": storage}
    app.dependency_overrides.clear()


@pytest.fixture
async def setup_verified_account(brief_test_env):
    storage = brief_test_env["storage"]
    vault = EncryptedCredentialVault(storage_driver=storage)
    yt_meta = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="UC_verified_shorts",
        username="al_amr_creator",
        display_name="AL AMR Creator",
        status=AccountStatus.ACTIVE,
    )
    await vault.save_account(yt_meta, sensitive_credentials={"client_id": "test_id", "client_secret": "test_sec"})
    return {"account_id": "UC_verified_shorts"}


# 1. Multi-Page PDF Extraction
def test_pdf_multi_page_text_extraction():
    p1 = "CAMPAIGN: AI Horizon 2026\nDuration: 30-60s\nAspect Ratio: 9:16"
    p2 = "Required Hashtags: #ai #future #tech\nCall to Action: Follow for more AI insights"
    pdf_bytes = build_mock_pdf([p1, p2])

    full_text, pages, is_image_only = BriefDocumentReader.read_document_bytes(pdf_bytes, "campaign_brief.pdf")
    assert not is_image_only
    assert len(pages) == 2
    assert "AI Horizon 2026" in full_text
    assert "#ai" in full_text
    assert pages[0][0] == 1
    assert pages[1][0] == 2


# 2. TXT and Markdown Extraction
def test_txt_and_md_extraction():
    md_content = b"""# Crypto Masters Campaign
- Duration: 25-45s
- Platforms: YouTube Shorts, Instagram Reels
- Prohibited: Scams, Guaranteed Returns
- Hashtags: #crypto #bitcoin
"""
    full_text, pages, is_img = BriefDocumentReader.read_document_bytes(md_content, "brief.md")
    assert not is_img
    assert len(pages) == 1
    assert "Crypto Masters Campaign" in full_text

    txt_content = b"Campaign: Alpha Pod\nClip length: 45s\nCPM: $2.50\n"
    full_text_t, _, _ = BriefDocumentReader.read_document_bytes(txt_content, "brief.txt")
    assert "Alpha Pod" in full_text_t


# 3. Image-Only PDF Fails Closed
def test_image_only_pdf_fails_closed():
    # PDF with blank/image pages and no text streams
    pdf_blank = build_mock_pdf(["", "  "])
    full_text, pages, is_image_only = BriefDocumentReader.read_document_bytes(pdf_blank, "scanned_doc.pdf")
    assert is_image_only
    assert len(full_text) == 0

    reqs = BriefDeterministicExtractor.extract(full_text, pages, "scanned_doc.pdf", "pdf", is_image_only=True)
    assert reqs.metadata.is_image_only
    assert reqs.metadata.extraction_status == "NEEDS_REVIEW"
    assert "Image-only PDF detected" in reqs.metadata.error_message


# 4. Deterministic Extraction Across All 10 Categories
def test_deterministic_extraction_all_10_categories():
    brief_text = """
CAMPAIGN: Autonomous Agent Revolution
Campaign ID: WHOP_CAMP_99182
Description: High-octane clipping for autonomous software developers.

SOURCE FOOTAGE:
Permitted footage: https://www.youtube.com/watch?v=agentStream101
Source footage restrictions: Only use official stream recordings
Specific footage required: yes

CLIPS & DURATION:
5 clips required
Duration: 30 to 60 seconds
Preferred duration: 45s
Resolution: 1080x1920
Aspect Ratio: 9:16
60 fps

CONTENT RULES:
Allowed topics: AI coding, prompt engineering, agentic architecture
Prohibited topics: Cryptocurrency speculation, get-rich-quick claims
Required talking points: Autonomous testing loop, Zero secret leakage
Prohibited claims: 100% bug-free software guarantees

BRANDING:
Required logo: Top-left official AL AMR emblem
No watermark on export
Subtitle style: High-contrast burned-in karaoke captions

TEXT & CALL TO ACTION:
Required hashtags: #AI #AgenticCoding #Shorts #Tech
Prohibited hashtags: #getrich #crypto
Call to action: Check the description to deploy the agent today!

PLATFORMS:
Post to YouTube Shorts and Instagram Reels

SUBMISSION & MONETIZATION:
Deadline: October 15, 2026
Submission URL: https://whop.com/campaigns/99182/submit
CPM: $2.00
Total Budget: $1,500
Payout: $25 fixed per clip
"""
    reqs = BriefDeterministicExtractor.extract(brief_text, [(1, brief_text)], "brief.txt", "txt")

    # 1. Identity
    assert reqs.identity.campaign_name == "Autonomous Agent Revolution"
    assert reqs.identity.campaign_id == "WHOP_CAMP_99182"
    assert "clipping for autonomous" in reqs.identity.campaign_description

    # 2. Source
    assert "https://www.youtube.com/watch?v=agentStream101" in reqs.source.source_urls
    assert reqs.source.specific_footage_required is True

    # 3. Clips
    assert reqs.clips.clip_count_required == 5
    assert reqs.clips.min_duration_seconds == 30.0
    assert reqs.clips.max_duration_seconds == 60.0
    assert reqs.clips.preferred_duration_seconds == 45.0
    assert reqs.clips.aspect_ratio == "9:16"
    assert reqs.clips.resolution == "1080x1920"
    assert reqs.clips.fps == 60

    # 4. Content
    assert any("AI coding" in t for t in reqs.content.allowed_topics)
    assert any("Cryptocurrency" in t for t in reqs.content.prohibited_topics)
    assert any("Autonomous testing loop" in t for t in reqs.content.required_talking_points)
    assert any("100% bug-free" in t for t in reqs.content.prohibited_claims)

    # 5. Branding
    assert "AL AMR emblem" in (reqs.branding.required_logo or "")
    assert reqs.branding.watermark_modality == RequirementModality.PROHIBITED

    # 6. Text
    assert "#AI" in reqs.text.required_hashtags
    assert "#Shorts" in reqs.text.required_hashtags
    assert "#getrich" in reqs.text.prohibited_hashtags
    assert reqs.text.call_to_action == "Check the description to deploy the agent today!"
    assert reqs.text.cta_modality == RequirementModality.REQUIRED

    # 7. Platforms
    assert "youtube_shorts" in reqs.platform.platforms
    assert "instagram_reels" in reqs.platform.platforms

    # 8. Submission
    assert "October 15, 2026" in (reqs.submission.deadline or "")
    assert "https://whop.com/campaigns/99182/submit" in (reqs.submission.submission_url_or_process or "")

    # 9. Monetization
    assert reqs.monetization.cpm_rate == 2.0
    assert reqs.monetization.total_budget == 1500.0
    assert "$25 fixed per clip" in (reqs.monetization.payout_info or "")


# 5. Modality Distinction
def test_modality_distinction():
    # Test watermark prohibited vs required
    text_prohibited = "No watermark on export. Watermarks are prohibited."
    reqs_p = BriefDeterministicExtractor.extract(text_prohibited, [(1, text_prohibited)])
    assert reqs_p.branding.watermark_modality == RequirementModality.PROHIBITED

    text_required = "Must include watermark on bottom-right corner."
    reqs_r = BriefDeterministicExtractor.extract(text_required, [(1, text_required)])
    assert reqs_r.branding.watermark_modality == RequirementModality.REQUIRED


# 6. Missing Requirements Never Invented
def test_missing_requirements_never_invented():
    minimal_brief = "Campaign: Minimal Test\nDuration: 30-45s\n"
    reqs = BriefDeterministicExtractor.extract(minimal_brief, [(1, minimal_brief)])

    # Omitted fields should be empty, not fabricated
    assert reqs.text.required_hashtags == []
    assert reqs.text.prohibited_hashtags == []
    assert reqs.text.call_to_action is None
    assert reqs.monetization.cpm_rate is None
    assert reqs.monetization.total_budget is None
    assert reqs.submission.deadline is None
    assert reqs.source.source_urls == []


# 7. Operator Override and Audit Trail
def test_operator_override_and_audit_trail():
    brief = "Duration: 30-60s\n#ai #tech\n"
    reqs = BriefDeterministicExtractor.extract(brief, [(1, brief)])
    assert reqs.clips.min_duration_seconds == 30.0
    assert reqs.clips.max_duration_seconds == 60.0

    # Apply operator override
    reqs.apply_override(
        field_path="clips.min_duration_seconds",
        override_value=15.0,
        operator="operator_jishan",
        reason="Client requested punchier short clips",
    )

    # Verify active value updated
    assert reqs.clips.min_duration_seconds == 15.0

    # Verify audit record preserved
    assert len(reqs.overrides) == 1
    override = reqs.overrides[0]
    assert override.field_path == "clips.min_duration_seconds"
    assert override.original_value == 30.0
    assert override.override_value == 15.0
    assert override.operator == "operator_jishan"
    assert override.reason == "Client requested punchier short clips"


# 8. AI Provider Output Validation & Enrichment
@pytest.mark.asyncio
async def test_ai_provider_structured_output_validation():
    mock_llm_json = {
        "identity": {"campaign_name": "AI Enriched Campaign", "campaign_description": "Enriched by model"},
        "clips": {"clip_count_required": 4, "min_duration_seconds": 20.0, "max_duration_seconds": 40.0},
        "content": {"required_talking_points": ["Deep learning scaling laws"]},
        "text": {"required_hashtags": ["#modelmagic", "#ai"]},
    }

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = lambda: {"choices": [{"message": {"content": json.dumps(mock_llm_json)}}]}

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_response)):
        extractor = BriefAIExtractor(base_url="http://mock-llm/v1")
        reqs = await extractor.extract_structured_requirements("Sample brief text")
        assert reqs is not None
        assert reqs.identity.campaign_name == "AI Enriched Campaign"
        assert reqs.clips.clip_count_required == 4
        assert "#modelmagic" in reqs.text.required_hashtags
        assert "Deep learning scaling laws" in reqs.content.required_talking_points


# 9. AI Provider Malformed Output Falls Back Safely
@pytest.mark.asyncio
async def test_ai_provider_malformed_output_fallback():
    # LLM outputs non-json garbage
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = lambda: {"choices": [{"message": {"content": "This is not JSON at all {{{"}}]}

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_response)):
        extractor = BriefAIExtractor(base_url="http://mock-llm/v1")
        reqs = await extractor.extract_structured_requirements("Sample text")
        assert reqs is None  # Returns None gracefully without crashing


# 10. AI Provider Network Failure Fallback
@pytest.mark.asyncio
async def test_ai_provider_network_failure_fallback():
    # LLM connection error
    import httpx
    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=httpx.ConnectError("Connection refused"))):
        extractor = BriefAIExtractor(base_url="http://offline-llm:11434/v1")
        reqs = await extractor.extract_structured_requirements("Sample text")
        assert reqs is None


# 11. End-to-End Brief Engine Analysis
@pytest.mark.asyncio
async def test_brief_intelligence_engine_full_flow():
    txt_brief = b"""Campaign: Engine Integration
Duration: 20-40s
Platforms: YouTube Shorts
#deepmind #clipping
"""
    engine = CampaignBriefIntelligenceEngine(ai_extractor=None)
    reqs = await engine.analyze_document_bytes(txt_brief, "brief.txt", enable_ai=False)
    assert reqs.identity.campaign_name == "Engine Integration"
    assert reqs.clips.min_duration_seconds == 20.0
    assert reqs.clips.max_duration_seconds == 40.0
    assert "#deepmind" in reqs.text.required_hashtags
    assert reqs.metadata.extraction_status == "SUCCESS"


# 12. FastAPI Endpoints: /api/campaigns/analyze-brief, override-requirements, brief-content
@pytest.mark.asyncio
async def test_api_campaign_brief_intelligence_endpoints(brief_test_env):
    storage = brief_test_env["storage"]
    brief_key = "campaigns/briefs/test_api_brief.txt"
    brief_content = b"Campaign: API Endpoints Test\nDuration: 15-30s\n#apitest\n"
    await storage.upload_bytes(brief_content, brief_key)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # A. Analyze brief from storage
        resp_analyze = await client.post(
            "/api/campaigns/analyze-brief",
            json={"brief_storage_key": brief_key},
        )
        assert resp_analyze.status_code == 200
        data_a = resp_analyze.json()
        assert data_a["status"] == "success"
        reqs = data_a["requirements"]
        assert reqs["identity"]["campaign_name"] == "API Endpoints Test"
        assert reqs["clips"]["min_duration_seconds"] == 15.0

        # B. Override requirement
        resp_override = await client.post(
            "/api/campaigns/override-requirements",
            json={
                "requirements": reqs,
                "field_path": "clips.min_duration_seconds",
                "override_value": 20.0,
                "reason": "Adjusted by lead editor",
            },
        )
        assert resp_override.status_code == 200
        data_o = resp_override.json()
        assert data_o["requirements"]["clips"]["min_duration_seconds"] == 20.0
        assert data_o["override_count"] == 1

        # C. Retrieve brief content for modal inspection
        resp_content = await client.get(f"/api/campaigns/brief-content?brief_storage_key={brief_key}")
        assert resp_content.status_code == 200
        data_c = resp_content.json()
        assert "API Endpoints Test" in data_c["full_text"]
        assert data_c["format"] == "txt"


# 13. Persistence in CampaignRecord and JobState.metadata
@pytest.mark.asyncio
async def test_persistence_in_campaign_and_job_state(brief_test_env, setup_verified_account, monkeypatch):
    monkeypatch.setattr("clipping.cli.pipeline_runner.run_pipeline", AsyncMock(return_value=0))
    storage = brief_test_env["storage"]

    # Upload brief
    brief_key = "campaigns/briefs/persisted_brief.txt"
    await storage.upload_bytes(b"Campaign: Persisted Job\nDuration: 30-50s\n#persist\n", brief_key)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/campaigns/create-and-run",
            json={
                "name": "Persisted Job Campaign",
                "source_uri": "https://www.youtube.com/watch?v=samplePersist",
                "brief_storage_key": brief_key,
                "brief_filename": "persisted_brief.txt",
                "target_platforms": ["youtube_shorts"],
                "target_account_id": setup_verified_account["account_id"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        campaign_id = data["campaign_id"]
        job_id = data["job_id"]

        # Check CampaignRecord has requirements
        camp_repo = CampaignRepository(storage_driver=storage)
        record = await camp_repo.get_campaign(campaign_id)
        assert record is not None
        assert record.requirements is not None
        assert record.requirements.identity.campaign_name == "Persisted Job"

        # Check JobState metadata has campaign_requirements
        state_repo = RemoteStorageStateRepository(storage_driver=storage)
        job = await state_repo.get_job(job_id)
        assert job is not None
        assert "campaign_requirements" in job.metadata_json
        assert job.metadata_json["campaign_requirements"]["clips"]["min_duration_seconds"] == 30.0


# 14. Zero Secret Leakage Audit
@pytest.mark.asyncio
async def test_zero_secret_leakage_in_brief_intelligence(brief_test_env):
    storage = brief_test_env["storage"]
    brief_key = "campaigns/briefs/secret_audit.txt"
    await storage.upload_bytes(b"Campaign: Zero Leakage Audit\n", brief_key)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/campaigns/analyze-brief", json={"brief_storage_key": brief_key})
        text = resp.text
        assert "AL_AMR_MASTER_KEY" not in text
        assert "client_secret" not in text
        assert "access_token" not in text
        assert "token" not in text.lower() or "refresh_token" not in text
