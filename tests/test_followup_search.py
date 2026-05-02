from __future__ import annotations

import csv
import json
from io import BytesIO
from pathlib import Path

import yaml
from pypdf import PdfWriter
from typer.testing import CliRunner

from doc_workbench import cli

runner = CliRunner()


def _sample_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": "Promoted Annual Report 2024"})
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _write_permissive_exec_policy(tmp_path: Path) -> Path:
    """Write a test-only permissive execution policy (all domains/MIME allowed).

    Tests using synthetic URLs (e.g. example.com) must opt in to the wildcard
    domain allowlist explicitly — it is no longer the shipping default.
    """
    policy_file = tmp_path / "test_exec_policy.yaml"
    policy_file.write_text(
        yaml.dump({
            "allowed_command_stages": ["discover", "review", "download", "followup-search", "scan"],
            "allowed_source_families": ["*"],
            "download": {
                "enabled": True,
                "max_count": 50,
                "max_file_size_bytes": 52_428_800,
                "allowed_mime_types": ["application/pdf", "text/html"],
            },
            "followup_search": {"enabled": True},
            "registry": {"root_restriction": "registry"},
        }),
        encoding="utf-8",
    )
    return policy_file

def test_followup_search_materializes_and_download_reuses_target(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    discover_dir = workspace / "runs" / "discover_seed"
    discover_dir.mkdir(parents=True)
    discover_json = discover_dir / "discover.json"
    discover_json.write_text(
        json.dumps(
            [
                {
                    "entity_id": "1001",
                    "name": "Example Corp",
                    "official_website": "https://example.com",
                    "status": "success",
                    "candidates": [
                        {
                            "entity_id": "1001",
                            "entity_name": "Example Corp",
                            "url": "https://search.example/result",
                            "title": "Example Corp annual report page",
                            "snippet": "search result",
                            "source_type": "search",
                            "source_tier": "search_same_domain",
                            "document_kind": "other",
                            "confidence": 0.55,
                            "reasons": ["search_result"],
                            "year": 2024,
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    async def fake_fetch(url: str, exec_policy=None) -> tuple[bytes, str, str]:
        if url == "https://search.example/result":
            html = b'<html><head><title>Seed</title></head><body><a href="https://example.com/annual-report-2024.pdf">Annual Report 2024</a></body></html>'
            return html, "text/html", url
        if url == "https://example.com/annual-report-2024.pdf":
            return _sample_pdf_bytes(), "application/pdf", url
        raise AssertionError(f"Unexpected URL: {url}")

    async def fail_download(_url: str, exec_policy=None) -> bytes:
        raise AssertionError("download_bytes should not be called when follow-up target is already materialized")

    import doc_workbench.acquisition.followup.workflow as workflow
    from doc_workbench.acquisition.followup.models import ResolvedTarget

    async def fake_resolve(pointer, exec_policy=None):
        return ResolvedTarget(
            original_url=pointer.url,
            resolved_url=pointer.url,
            final_url=pointer.url,
            content_type="application/pdf",
            status_code=200,
            is_accessible=True,
            pointer=pointer,
        )

    monkeypatch.setattr(workflow, "_fetch_url", fake_fetch)
    monkeypatch.setattr(workflow, "resolve_pointer", fake_resolve)
    monkeypatch.setattr(cli, "download_bytes", fail_download)

    exec_policy_file = _write_permissive_exec_policy(tmp_path)

    followup_result = runner.invoke(
        cli.app,
        [
            "followup-search",
            "--input", str(discover_json),
            "--workspace-root", str(workspace),
            "--execution-policy-path", str(exec_policy_file),
        ],
    )
    assert followup_result.exit_code == 0

    followup_runs = sorted((workspace / "runs").glob("followup_search_*"))
    enriched_path = followup_runs[-1] / "discover_enriched.json"
    enriched_payload = json.loads(enriched_path.read_text(encoding="utf-8"))
    followup_candidate = next(
        candidate
        for candidate in enriched_payload[0]["candidates"]
        if candidate.get("promotion_source") == "followup_search"
    )
    assert followup_candidate["followup_seed_document_id"]
    assert followup_candidate["followup_target_document_id"]

    review_result = runner.invoke(
        cli.app,
        ["review", "--input", str(enriched_path), "--workspace-root", str(workspace)],
    )
    assert review_result.exit_code == 0
    review_runs = sorted((workspace / "runs").glob("review_*"))
    review_csv = review_runs[-1] / "review_queue.csv"
    with review_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    promoted_row = next(row for row in rows if row.get("promotion_source") == "followup_search")
    assert promoted_row["recommendation"] == "approved"

    download_result = runner.invoke(
        cli.app,
        [
            "download",
            "--input", str(review_csv),
            "--workspace-root", str(workspace),
            "--execution-policy-path", str(exec_policy_file),
        ],
    )
    assert download_result.exit_code == 0

    from doc_workbench.registry.document_registry import DocumentRegistry

    registry = DocumentRegistry(workspace / "registry")
    annual_manifests = registry.list_manifests(artifact_family="annual_reports")
    assert len(annual_manifests) == 1
    assert annual_manifests[0]["artifact_family"] == "annual_reports"
    assert annual_manifests[0]["source_parent_document_id"] == followup_candidate["followup_target_document_id"]
