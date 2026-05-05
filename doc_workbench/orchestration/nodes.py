"""LangGraph node functions for the document acquisition workflow.

Each node receives the full WorkbenchState, performs one stage, and returns
a dict of updated keys.  LangGraph merges the returned dict back into state.
"""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

from doc_workbench.acquisition.discovery import (
    _followup_allowed,
    _top_candidate_fields,
    discover_entity,
    score_candidate,
)
from doc_workbench.acquisition.followup.workflow import run_followup_for_candidates
from doc_workbench.models import DiscoveryCandidate, DiscoveryRecord
from doc_workbench.observability.langfuse_bridge import get_langfuse_client
from doc_workbench.orchestration.state import WorkbenchState
from doc_workbench.policy import ContextPolicy
from doc_workbench.review.workflow import build_review_rows_from_records
from doc_workbench.execution_policy import PolicyViolationError, enforce_followup_search


# ---------------------------------------------------------------------------
# Telemetry helpers
# ---------------------------------------------------------------------------

def _sanitize_url_for_telemetry(url: str) -> str:
    """Reduce a URL to scheme+hostname before sending to remote telemetry.

    Paths can carry opaque document IDs, signed tokens, or sensitive filenames.
    Credentials embedded in the authority component (``user:pass@host``) are
    explicitly stripped.  Only the scheme and hostname (with safe port, if
    present) are forwarded to Langfuse; everything else (credentials, path,
    query, fragment) stays in local ``workspace/traces/`` artifacts only.

    Examples
    --------
    >>> _sanitize_url_for_telemetry("https://example.com/ar/2024.pdf?token=x")
    'https://example.com'
    >>> _sanitize_url_for_telemetry("https://user:pass@example.com/report.pdf")
    'https://example.com'
    >>> _sanitize_url_for_telemetry("")
    ''
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        # Build a clean netloc from hostname + optional port only.
        # parsed.netloc may contain "user:pass@host:port" — use parsed.hostname
        # and parsed.port instead to explicitly exclude credentials.
        hostname = parsed.hostname or ""
        if parsed.port:
            clean_netloc = f"{hostname}:{parsed.port}"
        else:
            clean_netloc = hostname
        return urlunparse((parsed.scheme, clean_netloc, "", "", "", ""))
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Node: discover
# ---------------------------------------------------------------------------

def discover_node(state: WorkbenchState) -> dict[str, Any]:
    """Run per-entity discovery (official site + regulatory + search).

    Returns the full raw candidate pool without dedup/sort/cap so that
    followup_node and rank_node operate on the complete set.  The final
    truncation to 10 candidates is owned by rank_node.

    Does NOT run follow-up extraction — that is delegated to followup_node.
    """
    entities = state["entities"]
    policy: ContextPolicy = state["policy"]
    tracer = state.get("tracer")
    lf = get_langfuse_client()

    exec_policy = state.get("exec_policy")

    async def _run_one(entity: Any) -> tuple[DiscoveryRecord, float]:
        t0 = time.perf_counter()
        record = await discover_entity(
            entity, followup_search=False, policy=policy, tracer=tracer,
            _skip_ranking=True, _force_skip_followup=True,
            exec_policy=exec_policy,
        )
        return record, (time.perf_counter() - t0) * 1000.0

    async def _run_all() -> list[tuple[DiscoveryRecord, float]]:
        return list(await asyncio.gather(*[_run_one(e) for e in entities]))

    results: list[tuple[DiscoveryRecord, float]] = asyncio.run(_run_all())
    records = [r for r, _ in results]

    if tracer is not None:
        for record, entity_ms in results:
            top_candidate = max(record.candidates, key=lambda c: c.confidence, default=None)
            top_url_local = top_candidate.url if top_candidate else ""
            top_conf_local = top_candidate.confidence if top_candidate else 0.0
            tracer.add_span(
                entity_id=record.entity.entity_id,
                stage="discover_entity_pre_rank",
                provider="orchestrator",
                latency_ms=entity_ms,
                candidate_count_in=0,
                candidate_count_out=len(record.candidates),
                top_candidate_url=top_url_local,
                top_confidence=top_conf_local,
                details={"ranking_deferred": True},
                retry_count=0,
            )

    if lf is not None:
        for record, entity_ms in results:
            # candidates are unsorted (ranking is deferred to rank_node);
            # compute the top candidate explicitly rather than assuming sorted order.
            top_candidate = max(record.candidates, key=lambda c: c.confidence, default=None)
            top_url = top_candidate.url if top_candidate else ""
            top_conf = top_candidate.confidence if top_candidate else 0.0
            lf.flush_span(
                stage="discover_entity_pre_rank",
                entity_id=record.entity.entity_id,
                latency_ms=entity_ms,
                candidate_count_in=0,
                candidate_count_out=len(record.candidates),
                top_candidate_url=_sanitize_url_for_telemetry(top_url),
                top_confidence=top_conf,
                ranking_deferred=True,
            )

    return {"discovery_records": records}


# ---------------------------------------------------------------------------
# Node: followup
# ---------------------------------------------------------------------------

def followup_node(state: WorkbenchState) -> dict[str, Any]:
    """Run follow-up extraction over search-sourced candidates.

    Enriches each DiscoveryRecord's candidate list with promoted follow-up
    candidates.  Produces followup_records (copy of discovery_records with
    followup candidates appended).

    Respects execution policy: if ``exec_policy.followup_search.enabled`` is
    ``False``, the node raises ``PolicyViolationError`` before any extraction.
    """
    records: list[DiscoveryRecord] = state.get("discovery_records", [])
    policy: ContextPolicy = state["policy"]
    exec_policy = state.get("exec_policy")
    followup_search: bool = state.get("followup_search", False)
    tracer = state.get("tracer")
    lf = get_langfuse_client()

    # Execution-policy enforcement: only enforce followup_search.enabled when
    # follow-up was actually requested. Enforcing unconditionally would cause
    # "discover --no-followup-search" with a policy that disables follow-up to
    # fail, while the legacy path succeeds (behaviour mismatch).
    if exec_policy is not None and followup_search:
        enforce_followup_search(exec_policy)

    enriched: list[DiscoveryRecord] = []

    async def _run_followup_for_record(record: DiscoveryRecord) -> tuple[DiscoveryRecord, float, list[DiscoveryCandidate]]:
        t0 = time.perf_counter()
        search_candidates = [c for c in record.candidates if c.source_type == "search"]
        followup_enabled, _ = _followup_allowed(
            policy=policy,
            followup_search=followup_search,
            official_candidates=[c for c in record.candidates if c.source_tier == "official"],
            regulatory_candidates=[c for c in record.candidates if c.source_tier == "regulatory"],
        )
        if not followup_enabled or not search_candidates:
            # No enrichment — return a deep copy to preserve stage isolation.
            return DiscoveryRecord(
                entity=record.entity,
                status=record.status,
                candidates=[copy.copy(c) for c in record.candidates],
                errors=list(record.errors),
            ), (time.perf_counter() - t0) * 1000.0, []

        seeds = [
            c for c in search_candidates
            if c.source_tier in policy.followup_search.allowed_seed_source_tiers
        ]
        if not seeds:
            return DiscoveryRecord(
                entity=record.entity,
                status=record.status,
                candidates=[copy.copy(c) for c in record.candidates],
                errors=list(record.errors),
            ), (time.perf_counter() - t0) * 1000.0, []

        errors: list[str] = list(record.errors)
        try:
            _results, promoted = await run_followup_for_candidates(
                record.entity, seeds, materialize=False, registry=None, exec_policy=exec_policy
            )
        except PolicyViolationError:
            raise
        except Exception as exc:
            errors.append(f"followup_node:{type(exc).__name__}: {exc}")
            return DiscoveryRecord(
                entity=record.entity,
                status=record.status,
                candidates=[copy.copy(c) for c in record.candidates],
                errors=errors,
            ), (time.perf_counter() - t0) * 1000.0, []

        # Keep raw promoted list for telemetry BEFORE dedup.
        raw_promoted = list(promoted)

        all_candidates = [copy.copy(c) for c in record.candidates] + promoted
        deduped: dict[str, DiscoveryCandidate] = {}
        for c in all_candidates:
            existing = deduped.get(c.url)
            if existing is None or c.confidence > existing.confidence:
                deduped[c.url] = c
        new_candidates = sorted(deduped.values(), key=lambda c: c.confidence, reverse=True)
        return DiscoveryRecord(
            entity=record.entity,
            status=record.status,
            candidates=new_candidates,
            errors=errors,
        ), (time.perf_counter() - t0) * 1000.0, raw_promoted

    async def _run_all() -> list[tuple[DiscoveryRecord, float, list[DiscoveryCandidate]]]:
        return list(await asyncio.gather(*[_run_followup_for_record(r) for r in records]))

    followup_results = asyncio.run(_run_all())
    enriched = [r for r, _, _ in followup_results]

    if tracer is not None:
        for orig, (enriched_record, entity_ms, raw_promoted) in zip(records, followup_results):
            # Count all search candidates as input — matching the legacy
            # followup_extraction span which uses len(search_candidates).
            # Use raw promoted (before dedup) for output counts — matching
            # legacy which traces len(followup_candidates) before dedup.
            search_in = [c for c in orig.candidates if c.source_type == "search"]
            followup_enabled, followup_reason = _followup_allowed(
                policy=policy,
                followup_search=followup_search,
                official_candidates=[c for c in orig.candidates if c.source_tier == "official"],
                regulatory_candidates=[c for c in orig.candidates if c.source_tier == "regulatory"],
            )
            top_url_local, top_conf_local = _top_candidate_fields(raw_promoted)
            tracer.add_span(
                entity_id=enriched_record.entity.entity_id,
                stage="followup_extraction",
                provider="followup_search",
                latency_ms=entity_ms,
                candidate_count_in=len(search_in),
                candidate_count_out=len(raw_promoted),
                top_candidate_url=top_url_local,
                top_confidence=top_conf_local,
                details={"enabled": followup_enabled, "reason": followup_reason},
                retry_count=0,
            )

    if lf is not None:
        for orig, (enriched_record, entity_ms_lf, raw_promoted_lf) in zip(records, followup_results):
            # Count all search candidates as input — matching legacy parity.
            # Use raw promoted (before dedup) for output counts.
            search_in_lf = [c for c in orig.candidates if c.source_type == "search"]
            followup_enabled_lf, followup_reason_lf = _followup_allowed(
                policy=policy,
                followup_search=followup_search,
                official_candidates=[c for c in orig.candidates if c.source_tier == "official"],
                regulatory_candidates=[c for c in orig.candidates if c.source_tier == "regulatory"],
            )
            top_url_lf, top_conf_lf = _top_candidate_fields(raw_promoted_lf)
            lf.flush_span(
                stage="followup_extraction",
                entity_id=enriched_record.entity.entity_id,
                latency_ms=entity_ms_lf,
                candidate_count_in=len(search_in_lf),
                candidate_count_out=len(raw_promoted_lf),
                top_candidate_url=_sanitize_url_for_telemetry(top_url_lf),
                top_confidence=top_conf_lf,
                enabled=followup_enabled_lf,
                reason=followup_reason_lf,
            )

    return {"followup_records": enriched}


# ---------------------------------------------------------------------------
# Node: rank
# ---------------------------------------------------------------------------

def rank_node(state: WorkbenchState) -> dict[str, Any]:
    """Deduplicate, re-score, sort, and cap candidates at 10 per entity.

    Operates on followup_records (or falls back to discovery_records).
    Returns fresh DiscoveryRecord objects — does not mutate the input records.
    """
    records: list[DiscoveryRecord] = state.get("followup_records") or state.get("discovery_records", [])
    policy: ContextPolicy = state["policy"]
    tracer = state.get("tracer")
    lf = get_langfuse_client()

    ranked: list[DiscoveryRecord] = []
    per_record_ms: list[float] = []

    for record in records:
        record_start = time.perf_counter()
        # Re-score all candidates through the policy — work on copies so
        # the source records (followup_records / discovery_records) are not
        # mutated and the two state keys remain independent.
        rescored: list[DiscoveryCandidate] = []
        for candidate in record.candidates:
            scored, _ = score_candidate(record.entity, copy.copy(candidate), policy)
            rescored.append(scored)
        # Dedup by URL, keep highest-confidence copy
        deduped: dict[str, DiscoveryCandidate] = {}
        for c in rescored:
            existing = deduped.get(c.url)
            if existing is None or c.confidence > existing.confidence:
                deduped[c.url] = c
        new_candidates = sorted(deduped.values(), key=lambda c: c.confidence, reverse=True)[:10]
        # Return a fresh record rather than mutating the shared input object.
        ranked.append(DiscoveryRecord(
            entity=record.entity,
            status=record.status,
            candidates=new_candidates,
            errors=list(record.errors),
        ))
        per_record_ms.append((time.perf_counter() - record_start) * 1000.0)

    if tracer is not None:
        for orig_record, ranked_record, entity_ms in zip(records, ranked, per_record_ms):
            top_url_local, top_conf_local = _top_candidate_fields(ranked_record.candidates)
            tracer.add_span(
                entity_id=ranked_record.entity.entity_id,
                stage="candidate_ranking",
                provider="ranking_policy",
                latency_ms=entity_ms,
                candidate_count_in=len(orig_record.candidates),
                candidate_count_out=len(ranked_record.candidates),
                top_candidate_url=top_url_local,
                top_confidence=top_conf_local,
                details={"ranking_deferred": False},
                retry_count=0,
            )

    if lf is not None:
        for orig_record, ranked_record, entity_ms in zip(records, ranked, per_record_ms):
            top_url, top_conf = _top_candidate_fields(ranked_record.candidates)
            lf.flush_span(
                stage="candidate_ranking",
                entity_id=ranked_record.entity.entity_id,
                latency_ms=entity_ms,
                candidate_count_in=len(orig_record.candidates),
                candidate_count_out=len(ranked_record.candidates),
                top_candidate_url=_sanitize_url_for_telemetry(top_url),
                top_confidence=top_conf,
                ranking_deferred=False,
            )

    return {"ranked_records": ranked}


# ---------------------------------------------------------------------------
# Node: review_prep
# ---------------------------------------------------------------------------

def review_prep_node(state: WorkbenchState) -> dict[str, Any]:
    """Build review rows from ranked records.

    Delegates to build_review_rows_from_records (record-list variant).
    Emits a local tracer span so the review stage appears in workspace/traces/,
    and a remote Langfuse span when observability is enabled.
    """
    records: list[DiscoveryRecord] = state.get("ranked_records") or state.get("discovery_records", [])
    policy: ContextPolicy = state["policy"]
    tracer = state.get("tracer")
    lf = get_langfuse_client()

    start = time.perf_counter()
    rows, review_trace, recommendation_summary = build_review_rows_from_records(records, policy)
    latency_ms = (time.perf_counter() - start) * 1000.0

    if tracer is not None:
        tracer.add_span(
            entity_id="all",
            stage="review_queue_generation",
            provider="review_policy",
            latency_ms=latency_ms,
            candidate_count_in=sum(len(r.candidates) for r in records),
            candidate_count_out=len(rows),
            recommendation_summary=recommendation_summary,
            retry_count=0,
        )

    if lf is not None:
        lf.flush_span(
            stage="review_queue_generation",
            entity_id="all",
            latency_ms=latency_ms,
            candidate_count_in=sum(len(r.candidates) for r in records),
            candidate_count_out=len(rows),
            top_candidate_url="",
            top_confidence=0.0,
            recommendation_summary=recommendation_summary,
        )

    return {
        "review_rows": rows,
        "review_trace": review_trace,
        "recommendation_summary": recommendation_summary,
    }
