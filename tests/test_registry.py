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


def test_entity_id_collision_does_not_cross_wire_dedupe(tmp_path: Path) -> None:
    """Two entity IDs that sanitize to the same directory prefix must not
    deduplicate each other's artifacts.

    "a/b" and "a_b" both normalize to "a_b" via _safe_path_component().
    Without the payload entity_id guard the second registration would be
    incorrectly flagged as a duplicate of the first.
    """
    registry = DocumentRegistry(tmp_path / "registry")

    bytes_a = _sample_pdf_bytes()
    result_a = registry.register_document(
        entity_id="a/b",
        entity_name="Corp A",
        source_url="https://example.com/a.pdf",
        family="annual_reports",
        doc_type="official_pdf",
        year="2024",
        pdf_bytes=bytes_a,
    )
    # Different entity ID that sanitizes identically, same content bytes and
    # URL pattern — this is the collision scenario that must NOT dedupe.
    # Pre-fix: _iter_manifest_paths would return "a/b"'s manifest for "a_b"
    # (same prefix), and the content_hash match would mark it as a duplicate.
    # Post-fix: payload entity_id check rejects the foreign manifest first.
    result_b = registry.register_document(
        entity_id="a_b",
        entity_name="Corp B",
        source_url="https://example.com/b.pdf",
        family="annual_reports",
        doc_type="official_pdf",
        year="2024",
        pdf_bytes=bytes_a,  # intentionally same bytes to trigger hash collision
    )
    assert result_b.is_duplicate is False, (
        "Distinct entity IDs that share a sanitized prefix must not cross-wire dedupe"
    )
    # document_ids may match (content-hash based) but they must be stored in
    # separate entity directories, confirming no cross-entity reuse occurred.
    assert result_a.document_folder != result_b.document_folder
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(_sample_pdf_bytes())
    result = scan_pdf(pdf_path)
    assert result["title"] == "Annual Report 2024"
    assert result["status"] == "complete"
