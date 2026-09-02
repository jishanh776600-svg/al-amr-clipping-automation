"""Docling Campaign Document Parser Implementation."""

import io
import tempfile
from typing import List, Optional
from clipping.contracts.campaign import CampaignSpec, BoundingBox
from clipping.document.base import (
    CampaignDocumentParser,
    DocumentExtractionResult,
    ExtractedBlock,
    ExtractedTable,
)
from clipping.document.extractor import DeterministicRuleExtractor
from clipping.storage.base import StorageDriver
from clipping.storage.keys import StorageKeyBuilder


class DoclingCampaignParser(CampaignDocumentParser):
    """
    Parses Campaign PDFs into CampaignSpec objects with provenance tracking.
    Uses Docling if available; falls back to structured pypdf parsing.
    """

    def __init__(self, use_docling_if_available: bool = True):
        self.use_docling = use_docling_if_available

    async def parse_bytes(
        self,
        pdf_bytes: bytes,
        campaign_id: str,
        raw_pdf_storage_key: Optional[str] = None
    ) -> CampaignSpec:
        if not pdf_bytes:
            raise ValueError("PDF bytes cannot be empty")

        doc_result = self._extract_document_structure(pdf_bytes)
        return DeterministicRuleExtractor.extract_spec(
            doc_result=doc_result,
            campaign_id=campaign_id,
            raw_pdf_storage_key=raw_pdf_storage_key,
        )

    async def parse_from_storage(
        self,
        storage_driver: StorageDriver,
        storage_key: str,
        campaign_id: str
    ) -> CampaignSpec:
        pdf_bytes = await storage_driver.download_bytes(storage_key)
        spec = await self.parse_bytes(
            pdf_bytes=pdf_bytes,
            campaign_id=campaign_id,
            raw_pdf_storage_key=storage_key,
        )

        # Persist extracted campaign_spec.json to storage vault
        spec_json = spec.model_dump_json(indent=2).encode("utf-8")
        spec_key = StorageKeyBuilder.campaign_spec_json(campaign_id)
        await storage_driver.upload_bytes(
            data=spec_json,
            storage_key=spec_key,
            content_type="application/json",
        )

        return spec

    def _extract_document_structure(self, pdf_bytes: bytes) -> DocumentExtractionResult:
        """Extracts text blocks, headings, tables, and provenance from PDF."""
        # Try Docling if installed and enabled
        if self.use_docling:
            try:
                from docling.document_converter import DocumentConverter
                return self._extract_with_docling(pdf_bytes)
            except ImportError:
                pass

        # Robust standard fallback using pypdf
        return self._extract_with_pypdf(pdf_bytes)

    def _extract_with_docling(self, pdf_bytes: bytes) -> DocumentExtractionResult:
        from docling.document_converter import DocumentConverter
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(pdf_bytes)
            tmp.flush()

            converter = DocumentConverter()
            res = converter.convert(tmp.name)
            doc = res.document

            blocks: List[ExtractedBlock] = []
            tables: List[ExtractedTable] = []

            for item in doc.texts:
                page_no = item.prov[0].page_no if item.prov else 1
                bbox = None
                if item.prov and item.prov[0].bbox:
                    b = item.prov[0].bbox
                    bbox = BoundingBox(page_no=page_no, left=b.l, top=b.t, right=b.r, bottom=b.b)

                is_heading = hasattr(item, "label") and "heading" in str(item.label).lower()
                blocks.append(
                    ExtractedBlock(
                        text=item.text,
                        page_no=page_no,
                        bbox=bbox,
                        is_heading=is_heading,
                    )
                )

            for table in doc.tables:
                page_no = table.prov[0].page_no if table.prov else 1
                bbox = None
                if table.prov and table.prov[0].bbox:
                    b = table.prov[0].bbox
                    bbox = BoundingBox(page_no=page_no, left=b.l, top=b.t, right=b.r, bottom=b.b)

                headers: List[str] = []
                rows: List[List[str]] = []
                if hasattr(table, "data") and hasattr(table.data, "table_cells"):
                    # Export table rows
                    pass

                tables.append(ExtractedTable(page_no=page_no, bbox=bbox, headers=headers, rows=rows))

            return DocumentExtractionResult(
                title=getattr(doc, "name", None),
                num_pages=getattr(doc, "num_pages", 1),
                blocks=blocks,
                tables=tables,
            )

    def _extract_with_pypdf(self, pdf_bytes: bytes) -> DocumentExtractionResult:
        from pypdf import PdfReader

        stream = io.BytesIO(pdf_bytes)
        try:
            reader = PdfReader(stream)
        except Exception as e:
            raise ValueError(f"Invalid or corrupted PDF file: {e}") from e

        num_pages = len(reader.pages)
        if num_pages == 0:
            raise ValueError("PDF document has 0 pages")

        blocks: List[ExtractedBlock] = []
        tables: List[ExtractedTable] = []
        first_heading = None

        for page_idx, page in enumerate(reader.pages):
            page_no = page_idx + 1
            text = page.extract_text() or ""
            lines = text.split("\n")

            for line in lines:
                line_clean = line.strip()
                if not line_clean:
                    continue

                # Check for simple markdown/pipe tables
                if "|" in line_clean:
                    parts = [p.strip() for p in line_clean.split("|") if p.strip()]
                    if parts:
                        tables.append(
                            ExtractedTable(
                                page_no=page_no,
                                bbox=BoundingBox(page_no=page_no, left=0.0, top=0.0, right=1.0, bottom=1.0),
                                headers=parts if not tables else tables[-1].headers,
                                rows=[parts],
                            )
                        )
                    continue

                # Identify headings
                is_heading = False
                if line_clean.isupper() and len(line_clean) < 80:
                    is_heading = True
                    if not first_heading:
                        first_heading = line_clean

                blocks.append(
                    ExtractedBlock(
                        text=line_clean,
                        page_no=page_no,
                        bbox=BoundingBox(page_no=page_no, left=0.05, top=0.1, right=0.95, bottom=0.9),
                        is_heading=is_heading,
                        heading_level=1 if is_heading else None,
                    )
                )

        return DocumentExtractionResult(
            title=first_heading,
            num_pages=num_pages,
            blocks=blocks,
            tables=tables,
        )
