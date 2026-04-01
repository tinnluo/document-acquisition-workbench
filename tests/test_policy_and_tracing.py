from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from doc_workbench import cli
from doc_workbench.models import DiscoveryCandidate, DiscoveryRecord

runner = CliRunner()


def test_discover_writes_policy_and_trace_artifacts(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    entities_csv = tmp_path / "entities.csv"
    entities_csv.write_text(
        "entity_id,name,ticker,official_website,cik,country\n1001,Example Corp,EXM,https://example.com,,US\n",
        encoding="utf-8",
    )

    async def fake_discover_entity(entity, *, followup_search=False, policy=None, tracer=None):
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
