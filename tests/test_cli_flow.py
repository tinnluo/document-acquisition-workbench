from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from doc_workbench import cli

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_permissive_exec_policy(tmp_path: Path) -> Path:
    """Write a test-only execution policy that allows all domains and MIME types.

    Tests that exercise the CLI with synthetic URLs (e.g. example.com) need to
    opt in to the wildcard domain allowlist explicitly — it is no longer the
    default.  Production code should always use a restricted allowlist.
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

    async def fake_download_async(url: str, exec_policy=None) -> tuple[bytes, str, str]:
        return fake_download(url), "application/pdf", url

    monkeypatch.setattr(cli, "download_bytes", fake_download_async)

    # Use a permissive test policy so synthetic example.com URLs are not
    # blocked by the domain allowlist (which no longer defaults to "*").
    exec_policy_file = _write_permissive_exec_policy(tmp_path)

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
        [
            "download",
            "--input", str(review_csv),
            "--workspace-root", str(workspace),
            "--execution-policy-path", str(exec_policy_file),
        ],
    )
    assert download_result.exit_code == 0

    scan_result = runner.invoke(
        cli.app,
        [
            "scan", "--all",
            "--workspace-root", str(workspace),
            "--execution-policy-path", str(exec_policy_file),
        ],
    )
    assert scan_result.exit_code == 0

    scan_runs = sorted((workspace / "runs").glob("scan_*"))
    scan_json = scan_runs[-1] / "scan_results.json"
    payload = json.loads(scan_json.read_text(encoding="utf-8"))
    assert payload[0]["title"] == "Annual Report 2024"


def test_download_fetch_attempt_count_enforces_egress_cap_on_repeated_failures(
    tmp_path: Path, monkeypatch
) -> None:
    """download.max_count must block outbound requests even when every attempt
    fails (e.g. MIME rejection, network error).

    Previously, download_count was only incremented on *successful* registration,
    so repeated failures could bypass the egress cap indefinitely.  The fix uses
    fetch_attempt_count (incremented before the network call) for enforcement.
    """
    import yaml

    workspace = tmp_path / "workspace"
    registry_dir = workspace / "registry"
    registry_dir.mkdir(parents=True)

    # Policy: max_count=1 means only 1 outbound fetch is permitted.
    policy_file = tmp_path / "strict_policy.yaml"
    policy_file.write_text(
        yaml.dump({
            "allowed_command_stages": ["download"],
            "allowed_source_families": ["*"],
            "download": {
                "enabled": True,
                "max_count": 1,
                "max_file_size_bytes": 52_428_800,
                "allowed_mime_types": ["application/pdf"],
            },
            "followup_search": {"enabled": False},
            "registry": {"root_restriction": ""},
        }),
        encoding="utf-8",
    )

    # Every download attempt raises an exception (simulates network error /
    # MIME rejection) so download_count stays at 0 permanently.
    call_count = 0

    async def always_fail(url: str, exec_policy=None):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Simulated network failure")

    monkeypatch.setattr(cli, "download_bytes", always_fail)

    # Build a review CSV with 3 approved rows.
    review_dir = workspace / "runs" / "review_seed"
    review_dir.mkdir(parents=True)
    review_csv = review_dir / "review_queue.csv"
    with review_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["entity_id", "entity_name", "url", "year",
                        "candidate_kind", "recommendation", "followup_target_document_id"],
        )
        writer.writeheader()
        for i in range(3):
            writer.writerow({
                "entity_id": f"100{i}",
                "entity_name": f"Corp {i}",
                "url": f"https://example.com/report{i}.pdf",
                "year": "2024",
                "candidate_kind": "official_pdf",
                "recommendation": "approved",
                "followup_target_document_id": "",
            })

    runner.invoke(
        cli.app,
        [
            "download",
            "--input", str(review_csv),
            "--workspace-root", str(workspace),
            "--execution-policy-path", str(policy_file),
        ],
    )

    # The run should exit with code 2 (PolicyViolationError) after the first
    # successful attempt is counted and the second row triggers the cap.
    # Crucially, only 1 outbound call should have been made (max_count=1
    # allows the first attempt; the second is blocked before the network call).
    assert call_count <= 1, (
        f"Egress cap not enforced: {call_count} outbound calls were made "
        "despite max_count=1.  fetch_attempt_count must enforce the cap "
        "before the network call regardless of prior success/failure."
    )

