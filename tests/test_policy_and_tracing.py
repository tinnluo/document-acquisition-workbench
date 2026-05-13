from __future__ import annotations

import json
import uuid
from pathlib import Path

from typer.testing import CliRunner

from doc_workbench import cli
from doc_workbench.models import DiscoveryCandidate, DiscoveryRecord
from doc_workbench.observability.tracer import RunTrace

runner = CliRunner()


def test_discover_writes_policy_and_trace_artifacts(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    entities_csv = tmp_path / "entities.csv"
    entities_csv.write_text(
        "entity_id,name,ticker,official_website,cik,country\n1001,Example Corp,EXM,https://example.com,,US\n",
        encoding="utf-8",
    )

    async def fake_discover_entity(entity, *, followup_search=False, policy=None, tracer=None, exec_policy=None):
        candidate = DiscoveryCandidate(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            url="https://example.com/annual-report-2024.pdf",
            title="Annual Report 2024",
            source_type="official_site",
            source_tier="official",
            document_kind="official_pdf",
            confidence=0.9,
            reasons=["same_domain", "pdf"],
            year=2024,
        )
        if tracer is not None:
            tracer.add_span(
                entity_id=entity.entity_id,
                stage="discover_entity",
                provider="orchestrator",
                latency_ms=1.0,
                candidate_count_in=0,
                candidate_count_out=1,
                top_candidate_url=candidate.url,
                top_confidence=candidate.confidence,
            )
        return DiscoveryRecord(entity=entity, status="success", candidates=[candidate], errors=[])

    monkeypatch.setattr(cli, "discover_entity", fake_discover_entity)

    result = runner.invoke(
        cli.app,
        ["discover", "--entities", str(entities_csv), "--workspace-root", str(workspace)],
    )
    assert result.exit_code == 0
    run_dir = next((workspace / "runs").glob("discover_*"))
    assert (run_dir / "resolved_policy.json").exists()
    assert (run_dir / "ranking_trace.json").exists()
    assert next((workspace / "traces").glob("discover_*.json"))


def test_review_writes_review_trace_and_trace_file(tmp_path: Path) -> None:
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
                            "url": "https://example.com/annual-report-2024.pdf",
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

    result = runner.invoke(
        cli.app,
        ["review", "--input", str(discover_json), "--workspace-root", str(workspace)],
    )
    assert result.exit_code == 0
    run_dir = next((workspace / "runs").glob("review_*"))
    assert (run_dir / "resolved_policy.json").exists()
    assert (run_dir / "review_trace.json").exists()
    trace_path = next((workspace / "traces").glob("review_*.json"))
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert payload["command"] == "review"


def test_policy_and_trace_summary_commands(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps(
            {
                "trace_id": "trace-1",
                "run_id": "discover_1",
                "command": "discover",
                "policy_digest": "abcd1234",
                "spans": [
                    {
                        "trace_id": "trace-1",
                        "entity_id": "1001",
                        "stage": "official_site_lookup",
                        "provider": "official_site",
                        "latency_ms": 12.5,
                        "candidate_count_in": 0,
                        "candidate_count_out": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    policy_result = runner.invoke(cli.app, ["policy"])
    assert policy_result.exit_code == 0
    assert "acquisition_order" in policy_result.output

    trace_result = runner.invoke(cli.app, ["trace-summary", "--input", str(trace_path)])
    assert trace_result.exit_code == 0
    assert "official_site_lookup" in trace_result.output


def test_trace_id_persisted_in_json(tmp_path: Path) -> None:
    """Verify that trace_id is persisted in the trace JSON file."""
    trace_id = uuid.uuid4().hex
    run_id = "test_run_123"

    tracer = RunTrace(
        trace_id=trace_id,
        run_id=run_id,
        command="discover",
        policy_digest="test_digest",
        exec_policy_digest="exec_digest"
    )

    tracer.add_span(
        entity_id="TEST_001",
        stage="test_stage",
        provider="test_provider",
        latency_ms=10.5,
        candidate_count_in=0,
        candidate_count_out=1,
    )

    trace_file = tmp_path / "test_trace.json"
    tracer.write(trace_file)

    payload = json.loads(trace_file.read_text(encoding="utf-8"))
    assert payload["trace_id"] == trace_id
    assert payload["run_id"] == run_id
    assert len(payload["spans"]) == 1
    assert payload["spans"][0]["trace_id"] == trace_id


def test_cli_discover_generates_and_persists_trace_id(tmp_path: Path, monkeypatch) -> None:
    """Verify that CLI discover command generates a trace_id and persists it in the trace JSON."""
    workspace = tmp_path / "workspace"
    entities_csv = tmp_path / "entities.csv"
    entities_csv.write_text(
        "entity_id,name,ticker,official_website,cik,country\n1001,Test Corp,TST,https://test.com,,US\n",
        encoding="utf-8",
    )

    async def fake_discover_entity(entity, *, followup_search=False, policy=None, tracer=None, exec_policy=None):
        candidate = DiscoveryCandidate(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            url="https://test.com/report.pdf",
            title="Test Report",
            source_type="official_site",
            source_tier="official",
            document_kind="official_pdf",
            confidence=0.9,
            reasons=["same_domain"],
            year=2024,
        )
        if tracer is not None:
            tracer.add_span(
                entity_id=entity.entity_id,
                stage="discover_entity",
                provider="test",
                latency_ms=1.0,
                candidate_count_in=0,
                candidate_count_out=1,
            )
        return DiscoveryRecord(entity=entity, status="success", candidates=[candidate], errors=[])

    monkeypatch.setattr(cli, "discover_entity", fake_discover_entity)

    result = runner.invoke(
        cli.app,
        ["discover", "--entities", str(entities_csv), "--workspace-root", str(workspace)],
    )
    assert result.exit_code == 0

    # Find the trace file
    trace_file = next((workspace / "traces").glob("discover_*.json"))
    payload = json.loads(trace_file.read_text(encoding="utf-8"))

    # Verify trace_id exists and is a valid 32-char hex string
    assert "trace_id" in payload
    trace_id = payload["trace_id"]
    assert len(trace_id) == 32, f"trace_id should be 32 chars, got {len(trace_id)}"
    assert all(c in "0123456789abcdef" for c in trace_id), f"trace_id should be lowercase hex, got {trace_id}"

    # Verify all spans share the same trace_id
    for span in payload.get("spans", []):
        assert span["trace_id"] == trace_id, "All spans should share the same trace_id"
