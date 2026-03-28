from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

from doc_workbench.registry.document_registry import DocumentRegistry
from doc_workbench.registry.metadata_scanner import scan_pdf


def _sample_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": "Annual Report 2024"})
    from io import BytesIO

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_registry_dedupes_by_url(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "registry")
    first = registry.register_document(
        entity_id="1001",
        entity_name="Example Corp",
        source_url="https://example.com/report.pdf",
        family="annual_reports",
        doc_type="official_pdf",
        year="2024",
        pdf_bytes=_sample_pdf_bytes(),
    )
    second = registry.register_document(
        entity_id="1001",
        entity_name="Example Corp",
        source_url="https://example.com/report.pdf",
        family="annual_reports",
        doc_type="official_pdf",
        year="2024",
        pdf_bytes=_sample_pdf_bytes(),
    )
    assert second.is_duplicate is True
    assert first.document_id == second.document_id


def test_scan_pdf_extracts_title(tmp_path: Path) -> None:
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(_sample_pdf_bytes())
    result = scan_pdf(pdf_path)
    assert result["title"] == "Annual Report 2024"
    assert result["status"] == "complete"
