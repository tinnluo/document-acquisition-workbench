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


def test_normalize_manifest_path_handles_old_absolute_paths(tmp_path: Path) -> None:
    """Verify backward compatibility: old manifests with absolute paths from
    different environments (e.g., /mnt/gcs/registry) are rebased to the
    current registry_root.
    """
    registry = DocumentRegistry(tmp_path / "registry")

    # Test new relative paths (post-migration)
    relative_path = registry._normalize_manifest_path("entity_123/annual_reports/2023/10-K/doc_abc/artifact.pdf")
    assert relative_path == tmp_path / "registry" / "entity_123/annual_reports/2023/10-K/doc_abc/artifact.pdf"

    # Test old absolute paths from GCS mount
    gcs_path = registry._normalize_manifest_path("/mnt/gcs/registry/entity_123/annual_reports/2023/10-K/doc_abc/artifact.pdf")
    assert gcs_path == tmp_path / "registry" / "entity_123/annual_reports/2023/10-K/doc_abc/artifact.pdf"

    # Test old absolute paths from local workspace (short form)
    local_path = registry._normalize_manifest_path("/workspace/registry/entity_456/annual_reports/2024/10-Q/doc_def/artifact.pdf")
    assert local_path == tmp_path / "registry" / "entity_456/annual_reports/2024/10-Q/doc_def/artifact.pdf"

    # Test old absolute paths from default local CLI (actual default case)
    default_local_path = registry._normalize_manifest_path("/Users/someone/project/workspace/registry/entity_789/annual_reports/2025/10-K/doc_ghi/artifact.pdf")
    assert default_local_path == tmp_path / "registry" / "entity_789/annual_reports/2025/10-K/doc_ghi/artifact.pdf"

    # Test absolute path with nested registry directories (edge case)
    nested_path = registry._normalize_manifest_path("/home/user/old-registry/workspace/registry/entity_999/annual_reports/2022/8-K/doc_jkl/artifact.pdf")
    assert nested_path == tmp_path / "registry" / "entity_999/annual_reports/2022/8-K/doc_jkl/artifact.pdf"

    # Test absolute path with multiple exact "registry" components (uses last occurrence)
    multi_registry_path = registry._normalize_manifest_path("/var/registry/cache/registry/entity_111/annual_reports/2021/10-K/doc_mno/artifact.pdf")
    assert multi_registry_path == tmp_path / "registry" / "entity_111/annual_reports/2021/10-K/doc_mno/artifact.pdf"

    # Test unknown absolute path (no "registry" component) - returns as-is
    unknown_path = registry._normalize_manifest_path("/unknown/path/artifact.pdf")
    assert unknown_path == Path("/unknown/path/artifact.pdf")

