"""Tests for the eval harness.

Verifies that:
- run_evals() runs without error against the bundled package fixtures
- latest_report.json is written with the correct schema
- All fixture cases pass (expected outputs match actual)

Imports from doc_workbench.evals.run_evals (the installed package path) so
that CI validates the same code path a wheel user would exercise.
"""

from __future__ import annotations

import json
from pathlib import Path

from doc_workbench.evals.run_evals import FIXTURES_DIR, run_evals


def test_run_evals_writes_report(tmp_path: Path) -> None:
    """run_evals should write latest_report.json with the required schema."""
    report_path = tmp_path / "latest_report.json"
    run_evals(fixtures_dir=FIXTURES_DIR, report_path=report_path)

    assert report_path.exists(), "latest_report.json was not written"

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert "aggregate" in payload
    assert "cases" in payload

    agg = payload["aggregate"]
    assert "total_cases" in agg
    assert "passed" in agg
    assert "failed" in agg
    assert "pass_rate" in agg
    assert "overall_passed" in agg


def test_run_evals_case_schema(tmp_path: Path) -> None:
    """Each case in the report should have the expected keys."""
    report_path = tmp_path / "latest_report.json"
    report = run_evals(fixtures_dir=FIXTURES_DIR, report_path=report_path)

    for case in report["cases"]:
        assert "entity_id" in case
        assert "passed" in case
        assert "metrics" in case
        assert "actual" in case
        assert "expected" in case
        metrics = case["metrics"]
        assert "candidate_recall" in metrics
        assert "top_ranked_correct" in metrics
        assert "top_recommendation_correct" in metrics


def test_run_evals_all_fixture_cases_pass(tmp_path: Path) -> None:
    """All bundled fixture cases should pass against the default policy."""
    report_path = tmp_path / "latest_report.json"
    report = run_evals(fixtures_dir=FIXTURES_DIR, report_path=report_path)

    failures = [c for c in report["cases"] if not c["passed"]]
    assert not failures, (
        f"{len(failures)} fixture case(s) failed:\n"
        + "\n".join(f"  {c['entity_id']}: actual={c['actual']}  expected={c['expected']}" for c in failures)
    )


def test_run_evals_aggregate_counts(tmp_path: Path) -> None:
    """Aggregate counts should be internally consistent."""
    report_path = tmp_path / "latest_report.json"
    report = run_evals(fixtures_dir=FIXTURES_DIR, report_path=report_path)

    agg = report["aggregate"]
    assert agg["passed"] + agg["failed"] == agg["total_cases"]
    assert 0.0 <= agg["pass_rate"] <= 1.0
