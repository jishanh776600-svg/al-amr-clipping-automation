"""Unit tests for Campaign Document Parsing & Rule Extraction."""

import io
import pytest
from clipping.document.docling_parser import DoclingCampaignParser
from clipping.document.base import (
    DocumentExtractionResult,
    ExtractedBlock,
    ExtractedTable,
)
from clipping.document.extractor import DeterministicRuleExtractor
from clipping.contracts.campaign import (
    CampaignRuleCategory,
    CampaignRuleSeverity,
    BoundingBox,
)
from clipping.storage.local import LocalStorageDriver


def create_mock_pdf_bytes(lines: list[str]) -> bytes:
    """Helper to generate standard PDF 1.4 bytes containing text lines."""
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


@pytest.mark.asyncio
async def test_parse_valid_campaign_pdf():
    lines = [
        "AI AUTOMATION CAMPAIGN",
        "Target Audience: Software Engineers and AI Builders",
        "Duration: 30-60s",
        "Required theme: How to automate vertical video clipping",
        "Prohibited: CompetitorX, ScamTools",
        "Tone: Professional and insightful",
        "Call to Action: Start building autonomous video clipping today!",
    ]
    pdf_bytes = create_mock_pdf_bytes(lines)

    parser = DoclingCampaignParser(use_docling_if_available=False)
    spec = await parser.parse_bytes(pdf_bytes, campaign_id="CAMP_2026_01")

    assert spec.campaign_id == "CAMP_2026_01"
    assert spec.min_duration_seconds == 30.0
    assert spec.max_duration_seconds == 60.0
    assert "Software Engineers" in spec.target_audience
    assert spec.required_cta_text == "Start building autonomous video clipping today!"

    # Verify extracted rules and provenance
    assert len(spec.rules) >= 3
    prohibited_rule = next(r for r in spec.rules if r.category == CampaignRuleCategory.PROHIBITED_WORD)
    assert prohibited_rule.severity == CampaignRuleSeverity.CRITICAL
    assert "CompetitorX" in prohibited_rule.exact_match_patterns
    assert prohibited_rule.provenance is not None
    assert prohibited_rule.provenance.page_no == 1


@pytest.mark.asyncio
async def test_parse_table_rules():
    # Test table extraction
    table = ExtractedTable(
        page_no=2,
        bbox=BoundingBox(page_no=2, left=0.1, top=0.2, right=0.9, bottom=0.8),
        headers=["Category", "Description", "Severity"],
        rows=[
            ["prohibited", "Never mention crypto or trading bots", "critical"],
            ["theme", "Focus on autonomous open-source pipelines", "critical"],
            ["brand", "Keep energy high and engaging", "warning"],
        ],
    )
    doc_result = DocumentExtractionResult(
        title="TABLE CAMPAIGN SPEC",
        num_pages=2,
        blocks=[],
        tables=[table],
    )

    spec = DeterministicRuleExtractor.extract_spec(doc_result, campaign_id="CAMP_TABLE_01")

    assert len(spec.rules) == 3
    assert spec.rules[0].category == CampaignRuleCategory.PROHIBITED_WORD
    assert spec.rules[0].provenance.page_no == 2
    assert spec.rules[2].category == CampaignRuleCategory.BRAND_VOICE
    assert spec.rules[2].severity == CampaignRuleSeverity.WARNING


@pytest.mark.asyncio
async def test_parse_missing_optional_fields():
    lines = [
        "MINIMAL CAMPAIGN",
        "Duration: 45s",
    ]
    pdf_bytes = create_mock_pdf_bytes(lines)

    parser = DoclingCampaignParser(use_docling_if_available=False)
    spec = await parser.parse_bytes(pdf_bytes, campaign_id="CAMP_MINIMAL")

    assert spec.target_audience == "General"
    assert spec.required_cta_text is None
    assert spec.min_duration_seconds == 35.0  # 45 - 10
    assert spec.max_duration_seconds == 55.0  # 45 + 10
    assert len(spec.rules) == 0


@pytest.mark.asyncio
async def test_parse_invalid_pdf_bytes():
    parser = DoclingCampaignParser(use_docling_if_available=False)

    with pytest.raises(ValueError, match="empty"):
        await parser.parse_bytes(b"", campaign_id="CAMP_EMPTY")

    with pytest.raises(ValueError, match="Invalid or corrupted"):
        await parser.parse_bytes(b"Not a valid PDF file at all", campaign_id="CAMP_CORRUPT")


@pytest.mark.asyncio
async def test_parse_from_storage(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)

    lines = [
        "VAULT CAMPAIGN",
        "Duration: 30-50s",
        "Target Audience: Content Creators",
    ]
    pdf_bytes = create_mock_pdf_bytes(lines)
    storage_key = "campaigns/CAMP_VAULT/raw_spec.pdf"
    await storage.upload_bytes(pdf_bytes, storage_key, content_type="application/pdf")

    parser = DoclingCampaignParser(use_docling_if_available=False)
    spec = await parser.parse_from_storage(storage, storage_key, campaign_id="CAMP_VAULT")

    assert spec.campaign_id == "CAMP_VAULT"
    assert spec.raw_pdf_storage_key == storage_key

    # Check that campaign_spec.json was persisted to storage vault
    spec_key = "campaigns/CAMP_VAULT/campaign_spec.json"
    assert await storage.exists(spec_key) is True
