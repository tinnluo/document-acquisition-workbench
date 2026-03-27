from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from doc_workbench import cli

runner = CliRunner()


def test_review_download_and_scan_flow(tmp_path: Path, monkeypatch) -> None:
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
                    "status": "success",
                    "candidates": [
                        {
                            "entity_id": "1001",
                            "entity_name": "Example Corp",
                            "url": "https://example.com/report.pdf",
                            "title": "Annual Report 2024",
                            "source_tier": "official",
                            "source_type": "official_site",
                            "document_kind": "official_pdf",
                            "confidence": 0.91,
                            "reasons": ["same_domain", "pdf"],
                            "year": 2024,
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    from pypdf import PdfWriter
    from io import BytesIO

    def fake_download(_url: str) -> bytes:
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.add_metadata({"/Title": "Annual Report 2024"})
        buffer = BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    async def fake_download_async(url: str) -> bytes:
        return fake_download(url)

    monkeypatch.setattr(cli, "download_bytes", fake_download_async)

    review_result = runner.invoke(
        cli.app,
        ["review", "--input", str(discover_json), "--workspace-root", str(workspace)],
    )
    assert review_result.exit_code == 0

    review_runs = sorted((workspace / "runs").glob("review_*"))
    review_csv = review_runs[-1] / "review_queue.csv"
    with review_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["recommendation"] == "approved"

    download_result = runner.invoke(
        cli.app,
        ["download", "--input", str(review_csv), "--workspace-root", str(workspace)],
    )
    assert download_result.exit_code == 0

    scan_result = runner.invoke(
        cli.app,
        ["scan", "--all", "--workspace-root", str(workspace)],
    )
    assert scan_result.exit_code == 0

    scan_runs = sorted((workspace / "runs").glob("scan_*"))
    scan_json = scan_runs[-1] / "scan_results.json"
    payload = json.loads(scan_json.read_text(encoding="utf-8"))
    assert payload[0]["title"] == "Annual Report 2024"
