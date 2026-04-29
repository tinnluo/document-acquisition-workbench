"""Tests for the LangGraph orchestration layer.

These tests verify:
- WorkbenchState is a valid TypedDict
- The StateGraph compiles without error
- Individual nodes mutate state correctly (with mocked I/O)
- The full graph produces DiscoveryRecord and ReviewRow outputs

Requires the ``[orchestration]`` optional extra (``langgraph``).  The entire
module is skipped automatically when the package is absent so that a plain
``pip install -e ".[dev]"`` environment passes ``pytest`` without errors.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# Skip this entire module if langgraph is not installed.
langgraph = pytest.importorskip("langgraph", reason="langgraph not installed (install with .[orchestration])")

from doc_workbench.models import DiscoveryCandidate, DiscoveryRecord, EntityRecord
from doc_workbench.observability.tracer import RunTrace
from doc_workbench.orchestration.graph import _build_graph, run_graph
from doc_workbench.orchestration.nodes import rank_node, review_prep_node
from doc_workbench.orchestration.state import WorkbenchState
from doc_workbench.policy import load_context_policy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entity(entity_id: str = "T001", name: str = "Test Corp") -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        name=name,
        ticker="TEST",
        official_website="https://testcorp.example.com",
        cik="",
        country="US",
    )


def _make_candidate(entity: EntityRecord, url: str, confidence: float = 0.65, source_tier: str = "official") -> DiscoveryCandidate:
    return DiscoveryCandidate(
        entity_id=entity.entity_id,
        entity_name=entity.name,
        url=url,
        title="Annual Report 2023",
        snippet="official site",
        source_type="official_site",
        source_tier=source_tier,
        document_kind="official_pdf",
        year=2023,
        confidence=confidence,
        reasons=["same_domain", "pdf"],
    )


def _make_record(entity: EntityRecord, candidates: list[DiscoveryCandidate] | None = None) -> DiscoveryRecord:
    if candidates is None:
        candidates = [
            _make_candidate(entity, f"https://testcorp.example.com/annual-report-2023.pdf")
        ]
    return DiscoveryRecord(entity=entity, status="success", candidates=candidates)


def _base_state(tmp_path: Path) -> WorkbenchState:
    policy = load_context_policy()
    entity = _make_entity()
    tracer = RunTrace(trace_id="test-run", run_id="test-run", command="discover", policy_digest=policy.digest)
    return {
        "entities": [entity],
        "policy": policy,
        "tracer": tracer,
        "output_dir": tmp_path,
        "followup_search": False,
    }


# ---------------------------------------------------------------------------
# Graph compilation
# ---------------------------------------------------------------------------

def test_graph_compiles() -> None:
    """StateGraph should compile to a callable without raising."""
    graph = _build_graph()
    assert callable(graph.invoke)


# ---------------------------------------------------------------------------
# rank_node
# ---------------------------------------------------------------------------

def test_rank_node_deduplicates_and_caps(tmp_path: Path) -> None:
    """rank_node should dedup by URL and cap at 10 candidates per entity."""
    policy = load_context_policy()
    entity = _make_entity()
    # Create 15 candidates with the same URL pattern (some duplicates)
    candidates = []
    for i in range(15):
        url = f"https://testcorp.example.com/report-{i % 12}.pdf"  # 12 unique URLs
        candidates.append(_make_candidate(entity, url, confidence=0.5 + i * 0.01))
    record = DiscoveryRecord(entity=entity, status="success", candidates=candidates)

    state: WorkbenchState = {
        "entities": [entity],
        "policy": policy,
        "tracer": None,
        "output_dir": tmp_path,
        "followup_search": False,
        "followup_records": [record],
    }
    result = rank_node(state)
    ranked = result["ranked_records"]
    assert len(ranked) == 1
    assert len(ranked[0].candidates) <= 10
    # Verify sorted by confidence descending
    confidences = [c.confidence for c in ranked[0].candidates]
    assert confidences == sorted(confidences, reverse=True)


def test_rank_node_falls_back_to_discovery_records(tmp_path: Path) -> None:
    """rank_node should use discovery_records when followup_records is absent."""
    policy = load_context_policy()
    entity = _make_entity()
    record = _make_record(entity)

    state: WorkbenchState = {
        "entities": [entity],
        "policy": policy,
        "tracer": None,
        "output_dir": tmp_path,
        "followup_search": False,
        "discovery_records": [record],
    }
    result = rank_node(state)
    assert "ranked_records" in result
    assert len(result["ranked_records"]) == 1


# ---------------------------------------------------------------------------
# review_prep_node
# ---------------------------------------------------------------------------

def test_review_prep_node_produces_review_rows(tmp_path: Path) -> None:
    """review_prep_node should produce review_rows, review_trace, and recommendation_summary."""
    policy = load_context_policy()
    entity = _make_entity()
    record = _make_record(entity)

    state: WorkbenchState = {
        "entities": [entity],
        "policy": policy,
        "tracer": None,
        "output_dir": tmp_path,
        "followup_search": False,
        "ranked_records": [record],
    }
    result = review_prep_node(state)
    assert "review_rows" in result
    assert "review_trace" in result
    assert "recommendation_summary" in result
    assert len(result["review_rows"]) == len(record.candidates)
    assert isinstance(result["recommendation_summary"], dict)
    assert set(result["recommendation_summary"].keys()) >= {"approved", "needs_review", "rejected"}


def test_review_prep_node_approved_candidate(tmp_path: Path) -> None:
    """A high-confidence same-domain PDF candidate should be 'approved'."""
    policy = load_context_policy()
    entity = _make_entity()
    candidate = DiscoveryCandidate(
        entity_id=entity.entity_id,
        entity_name=entity.name,
        url="https://testcorp.example.com/annual-report-2023.pdf",
        title="Annual Report 2023 PDF",
        snippet="official site",
        source_type="official_site",
        source_tier="official",
        document_kind="official_pdf",
        year=2023,
        confidence=0.91,
        reasons=["same_domain", "pdf"],
    )
    record = DiscoveryRecord(entity=entity, status="success", candidates=[candidate])
    state: WorkbenchState = {
        "entities": [entity],
        "policy": policy,
        "tracer": None,
        "output_dir": tmp_path,
        "followup_search": False,
        "ranked_records": [record],
    }
    result = review_prep_node(state)
    assert result["review_rows"][0].recommendation == "approved"


# ---------------------------------------------------------------------------
# Full graph run (mocked I/O)
# ---------------------------------------------------------------------------

def test_run_graph_produces_ranked_records(tmp_path: Path) -> None:
    """Default (discover) graph run produces ranked_records but not review_rows."""
    policy = load_context_policy()
    entity = _make_entity()
    tracer = RunTrace(trace_id="test", run_id="test", command="discover", policy_digest=policy.digest)

    async def _fake_discover(ent, *, followup_search=False, policy=None, tracer=None, _skip_ranking=False, _force_skip_followup=False):
        return _make_record(ent, [_make_candidate(ent, "https://testcorp.example.com/ar.pdf", confidence=0.91)])

    with patch("doc_workbench.orchestration.nodes.discover_entity", new=_fake_discover):
        final_state = run_graph(
            entities=[entity],
            policy=policy,
            tracer=tracer,
            output_dir=tmp_path,
            followup_search=False,
        )

    assert "ranked_records" in final_state
    assert len(final_state["ranked_records"]) == 1
    # Discover graph stops at rank — no review_rows
    assert "review_rows" not in final_state


def test_run_graph_full_mode_produces_review_rows(tmp_path: Path) -> None:
    """Full-mode graph run produces ranked_records and review_rows for each entity."""
    policy = load_context_policy()
    entity = _make_entity()
    tracer = RunTrace(trace_id="test", run_id="test", command="discover", policy_digest=policy.digest)

    async def _fake_discover(ent, *, followup_search=False, policy=None, tracer=None, _skip_ranking=False, _force_skip_followup=False):
        return _make_record(ent, [_make_candidate(ent, "https://testcorp.example.com/ar.pdf", confidence=0.91)])

    with patch("doc_workbench.orchestration.nodes.discover_entity", new=_fake_discover):
        final_state = run_graph(
            entities=[entity],
            policy=policy,
            tracer=tracer,
            output_dir=tmp_path,
            followup_search=False,
            mode="full",
        )

    assert "ranked_records" in final_state
    assert len(final_state["ranked_records"]) == 1
    assert "review_rows" in final_state
    assert len(final_state["review_rows"]) >= 1
    # Top candidate should be approved
    assert final_state["review_rows"][0].recommendation == "approved"
