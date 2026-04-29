"""Eval harness for document-acquisition-workbench.

Runs reproducible benchmark cases against the ranking and review pipeline
using fixture data, then writes a machine-readable report.

The harness exercises the real ``rank_node`` from the LangGraph orchestration
layer.  Fixtures may deliberately present candidates in a non-optimal order to
verify that rank_node correctly reorders them before review classification.

Usage
-----
    doc-workbench eval
    # or directly:
    python -m doc_workbench.evals.run_evals

Metrics per fixture case
------------------------
- ``candidate_recall``       : fraction of expected URLs present after ranking
- ``top_ranked_correct``     : top-ranked URL matches expected
- ``top_recommendation_correct`` : top candidate's recommendation class matches
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from doc_workbench.models import DiscoveryCandidate, DiscoveryRecord, EntityRecord
from doc_workbench.observability.langfuse_bridge import reset_langfuse_client
from doc_workbench.observability.tracer import RunTrace
from doc_workbench.orchestration.nodes import rank_node, review_prep_node
from doc_workbench.orchestration.state import WorkbenchState
from doc_workbench.policy import load_context_policy

# Paths resolved relative to this file so they work after installation.
_PACKAGE_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = _PACKAGE_DIR / "fixtures"
DEFAULT_REPORT_PATH = Path.cwd() / "evals" / "latest_report.json"


def _load_fixture(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(
            f"{path.name}: fixture file must contain a JSON array at the top level, "
            f"got {type(raw).__name__}"
        )
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{path.name}[{idx}]: each fixture entry must be a JSON object (dict), "
                f"got {type(entry).__name__}"
            )
    return raw


def _validate_fixture_entry(raw: dict[str, Any], path: Path, idx: int) -> None:
    """Raise ValueError if a fixture entry is missing required fields.

    Comment-only entries (those carrying only a ``_comment`` key and no
    ``entity_id``) are explicitly excluded before this function is called.
    """
    def _require(field: str) -> None:
        if not raw.get(field):
            raise ValueError(
                f"{path.name}[{idx}]: missing required field '{field}'"
            )

    _require("entity_id")
    _require("name")
    if not isinstance(raw.get("candidates"), list):
        raise ValueError(f"{path.name}[{idx}]: 'candidates' must be a list")
    if not raw["candidates"]:
        raise ValueError(f"{path.name}[{idx}]: 'candidates' list is empty")

    for c_idx, c in enumerate(raw["candidates"]):
        if not isinstance(c, dict):
            raise ValueError(
                f"{path.name}[{idx}].candidates[{c_idx}]: each candidate must be a dict, got {type(c).__name__}"
            )
        if not c.get("url"):
            raise ValueError(
                f"{path.name}[{idx}].candidates[{c_idx}]: missing required field 'url'"
            )

    expected = raw.get("_expected")
    if not isinstance(expected, dict) or not expected:
        raise ValueError(f"{path.name}[{idx}]: '_expected' must be a non-empty dict")
    for req in ("top_url", "top_recommendation"):
        if req not in expected:
            raise ValueError(f"{path.name}[{idx}]._expected: missing required field '{req}'")
    for str_field in ("top_url", "top_recommendation"):
        val = expected.get(str_field)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(
                f"{path.name}[{idx}]._expected.{str_field}: must be a non-empty string, got {val!r}"
            )
    if "recall_urls" in expected:
        recall = expected["recall_urls"]
        if not isinstance(recall, list) or not all(isinstance(u, str) for u in recall):
            raise ValueError(
                f"{path.name}[{idx}]._expected.recall_urls: must be a list of strings"
            )


def _is_comment_only(raw: dict[str, Any]) -> bool:
    """Return True only if the entry is non-empty and every key is a comment annotation.

    An empty dict is NOT considered comment-only — it is a malformed entry and
    should fail validation.  A truly comment-only entry must have at least one
    key, and all keys must start with ``_comment``.

    A truly comment-only entry has no data fields — it exists purely to
    annotate the fixture file.  An entry that has ``_comment`` alongside
    real fields (e.g. ``name``, ``candidates``) is NOT comment-only and
    must be validated normally.
    """
    return bool(raw) and all(k.startswith("_comment") for k in raw)


def _fixture_to_records(raw_fixtures: list[dict[str, Any]], path: Path) -> tuple[list[DiscoveryRecord], list[dict[str, Any]]]:
    """Parse fixture dicts into DiscoveryRecord objects and return (records, expected_list).

    Truly comment-only entries (where every key starts with ``_comment``) are
    skipped.  All other entries — including those that mix ``_comment`` with
    real fields — are validated strictly and must have a valid ``entity_id``.
    """
    records: list[DiscoveryRecord] = []
    expected_list: list[dict[str, Any]] = []

    for idx, raw in enumerate(raw_fixtures):
        if _is_comment_only(raw):
            continue  # purely decorative comment entry — skip
        if not raw.get("entity_id"):
            raise ValueError(
                f"{path.name}[{idx}]: entry has no 'entity_id' — "
                "only purely comment-only entries (all keys start with '_comment') may be skipped"
            )
        _validate_fixture_entry(raw, path, idx)
        entity = EntityRecord(
            entity_id=str(raw["entity_id"]),
            name=str(raw.get("name") or ""),
            ticker=str(raw.get("ticker") or ""),
            official_website=str(raw.get("official_website") or ""),
            cik=str(raw.get("cik") or ""),
            country=str(raw.get("country") or ""),
        )
        candidates = [
            DiscoveryCandidate(
                entity_id=str(c.get("entity_id") or entity.entity_id),
                entity_name=str(c.get("entity_name") or entity.name),
                url=str(c["url"]),
                title=str(c.get("title") or ""),
                snippet=str(c.get("snippet") or ""),
                source_type=str(c.get("source_type") or ""),
                source_tier=str(c.get("source_tier") or ""),
                document_kind=str(c.get("document_kind") or ""),
                year=c.get("year"),
                confidence=float(c.get("confidence") or 0.0),
                reasons=list(c.get("reasons") or []),
            )
            for c in raw["candidates"]
            if c.get("url")  # skip _comment-only sub-entries (already validated above)
        ]
        records.append(DiscoveryRecord(entity=entity, status=str(raw.get("status") or "no_result"), candidates=candidates))
        expected_list.append(dict(raw["_expected"]))

    # Sanity-check: every non-comment entry must have produced a record.
    expected_count = sum(1 for raw in raw_fixtures if not _is_comment_only(raw))
    if len(records) != expected_count:
        raise ValueError(
            f"{path.name}: parsed {len(records)} records but expected {expected_count} "
            "data entries — check for silent drops"
        )

    return records, expected_list


def _run_rank_then_review(
    records: list[DiscoveryRecord],
    policy: Any,
) -> tuple[list[DiscoveryRecord], list[Any], dict[str, int]]:
    """Run rank_node then review_prep_node over in-memory records.

    This exercises the real LangGraph node functions rather than calling
    build_review_rows_from_records directly.

    Langfuse remote tracing is explicitly disabled for eval runs to prevent
    fixture data from being sent to remote telemetry.  The bridge is reset
    after the run so subsequent code (if any) can re-initialise normally.
    """
    # Suppress Langfuse for the duration of this eval run.
    _prev_flag = os.environ.pop("DOC_WORKBENCH_ENABLE_LANGFUSE", None)
    reset_langfuse_client()
    try:
        tracer = RunTrace(trace_id="eval", run_id="eval", command="eval", policy_digest=policy.digest)

        rank_state: WorkbenchState = {
            "entities": [r.entity for r in records],
            "policy": policy,
            "tracer": tracer,
            "output_dir": Path("."),
            "followup_search": False,
            "followup_records": records,
        }
        rank_result = rank_node(rank_state)
        ranked_records: list[DiscoveryRecord] = rank_result["ranked_records"]

        review_state: WorkbenchState = {**rank_state, **rank_result}
        review_result = review_prep_node(review_state)

        return ranked_records, review_result["review_rows"], review_result["recommendation_summary"]
    finally:
        # Restore env and reset the bridge so callers can re-initialise normally.
        if _prev_flag is not None:
            os.environ["DOC_WORKBENCH_ENABLE_LANGFUSE"] = _prev_flag
        reset_langfuse_client()


def _eval_fixture_case(
    ranked_record: DiscoveryRecord,
    expected: dict[str, Any],
    review_rows_by_entity: dict[str, list[Any]],
) -> dict[str, Any]:
    """Evaluate a single fixture entity against its expected outputs."""
    entity_id = ranked_record.entity.entity_id
    ranked_urls = [c.url for c in ranked_record.candidates]

    # Metric 1: candidate recall
    recall_urls: list[str] = expected.get("recall_urls") or []
    if recall_urls:
        hits = sum(1 for url in recall_urls if url in ranked_urls)
        candidate_recall = hits / len(recall_urls)
    else:
        candidate_recall = 1.0

    # Metric 2: top ranked URL (after rank_node)
    top_url_actual = ranked_urls[0] if ranked_urls else ""
    top_url_expected = expected.get("top_url") or ""
    top_ranked_correct = top_url_actual == top_url_expected

    # Metric 3: top recommendation class (after review_prep_node)
    entity_rows = review_rows_by_entity.get(entity_id, [])
    top_recommendation_actual = entity_rows[0].recommendation if entity_rows else "no_rows"
    top_recommendation_expected = expected.get("top_recommendation") or ""
    top_recommendation_correct = top_recommendation_actual == top_recommendation_expected

    passed = (
        candidate_recall >= 1.0
        and top_ranked_correct
        and top_recommendation_correct
    )

    return {
        "entity_id": entity_id,
        "entity_name": ranked_record.entity.name,
        "passed": passed,
        "metrics": {
            "candidate_recall": round(candidate_recall, 3),
            "top_ranked_correct": top_ranked_correct,
            "top_recommendation_correct": top_recommendation_correct,
        },
        "actual": {
            "top_url": top_url_actual,
            "top_recommendation": top_recommendation_actual,
            "ranked_url_count": len(ranked_urls),
        },
        "expected": {
            "top_url": top_url_expected,
            "top_recommendation": top_recommendation_expected,
            "recall_urls": recall_urls,
        },
    }


def run_evals(
    fixtures_dir: Path = FIXTURES_DIR,
    report_path: Path = DEFAULT_REPORT_PATH,
) -> dict[str, Any]:
    """Run all eval fixture cases and write a machine-readable report.

    Pipeline per fixture file:
      1. Parse fixture JSON into DiscoveryRecord objects
      2. Run rank_node (re-score, dedup, sort, cap)
      3. Run review_prep_node (classify, produce ReviewRow list)
      4. Compare actual outputs against _expected fields

    Returns the full report dict.
    """
    if not fixtures_dir.exists() or not fixtures_dir.is_dir():
        raise FileNotFoundError(
            f"Fixtures directory not found or is not a directory: {fixtures_dir}"
        )
    fixture_paths = sorted(fixtures_dir.glob("*.json"))
    if not fixture_paths:
        raise FileNotFoundError(
            f"No *.json fixture files found in: {fixtures_dir}"
        )

    policy = load_context_policy()
    fixture_results: list[dict[str, Any]] = []

    for fixture_path in fixture_paths:
        raw_fixtures = _load_fixture(fixture_path)
        records, expected_list = _fixture_to_records(raw_fixtures, fixture_path)

        ranked_records, rows, _summary = _run_rank_then_review(records, policy)

        rows_by_entity: dict[str, list[Any]] = {}
        for row in rows:
            rows_by_entity.setdefault(row.entity_id, []).append(row)

        for ranked_record, expected in zip(ranked_records, expected_list, strict=True):
            result = _eval_fixture_case(ranked_record, expected, rows_by_entity)
            result["fixture_file"] = fixture_path.name
            fixture_results.append(result)

    total = len(fixture_results)
    passed = sum(1 for r in fixture_results if r["passed"])
    aggregate = {
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "overall_passed": passed == total,
    }

    report: dict[str, Any] = {
        "aggregate": aggregate,
        "cases": fixture_results,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> None:
    report = run_evals()
    agg = report["aggregate"]
    print(f"Evals: {agg['passed']}/{agg['total_cases']} passed  (pass_rate={agg['pass_rate']})")
    print(f"Report written to: {DEFAULT_REPORT_PATH}")
    if not agg["overall_passed"]:
        for case in report["cases"]:
            if not case["passed"]:
                print(f"  FAIL  {case['entity_id']}  actual={case['actual']}  expected={case['expected']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
