from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from doc_workbench.acquisition.followup.workflow import run_followup_for_candidates
from doc_workbench.http_utils import safe_get
from doc_workbench.models import DiscoveryCandidate, DiscoveryRecord, EntityRecord
from doc_workbench.observability.tracer import RunTrace
from doc_workbench.policy import ContextPolicy
from doc_workbench.execution_policy import PolicyViolationError
from doc_workbench.providers.regulatory_filings import RegulatoryFilingsProvider
from doc_workbench.providers.search import get_search_provider

KEYWORD_RE = re.compile(r"(annual|report|investor|financial|10-k|20-f|results)", re.IGNORECASE)
YEAR_RE = re.compile(r"(20\d{2})")


def load_entities(path: Path) -> list[EntityRecord]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            rows.append(
                EntityRecord(
                    entity_id=str(row.get("entity_id") or "").strip(),
                    name=str(row.get("name") or "").strip(),
                    ticker=str(row.get("ticker") or "").strip(),
                    official_website=str(row.get("official_website") or "").strip(),
                    cik=str(row.get("cik") or "").strip(),
                    country=str(row.get("country") or "").strip(),
                )
            )
        return rows


def _normalize_official_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.netloc}{parsed.path or '/'}"


def _extract_year(value: str) -> int | None:
    match = YEAR_RE.search(value)
    return int(match.group(1)) if match else None


def _same_domain(url: str, official_website: str) -> bool:
    if not official_website:
        return False
    candidate_domain = urlparse(url).netloc.lower().removeprefix("www.")
    official_domain = urlparse(_normalize_official_url(official_website)).netloc.lower().removeprefix("www.")
    return bool(candidate_domain and official_domain and candidate_domain.endswith(official_domain))


def score_candidate(
    entity: EntityRecord,
    candidate: DiscoveryCandidate,
    policy: ContextPolicy,
) -> tuple[DiscoveryCandidate, dict[str, Any]]:
    same_domain_match = _same_domain(candidate.url, entity.official_website)
    pdf_bonus = 0.15 if candidate.url.lower().endswith(".pdf") else 0.0
    keyword_match = bool(KEYWORD_RE.search(f"{candidate.title} {candidate.url}"))
    keyword_bonus = 0.1 if keyword_match else 0.0
    official_bonus = 0.05 if candidate.source_tier == "official" else 0.0
    same_domain_bonus = (
        float(policy.same_domain_preference.score_bonus)
        if policy.same_domain_preference.enabled and same_domain_match
        else 0.0
    )
    score = 0.35 + same_domain_bonus + pdf_bonus + keyword_bonus + official_bonus
    candidate.confidence = min(score, 0.99)
    reasons = set(candidate.reasons)
    if same_domain_match:
        reasons.add("same_domain")
    if pdf_bonus:
        reasons.add("pdf")
    if keyword_bonus:
        reasons.add("annual_report_terms")
    candidate.reasons = sorted(reasons)
    if candidate.url.lower().endswith(".pdf"):
        candidate.document_kind = candidate.document_kind or "official_pdf"
    breakdown = {
        "entity_id": entity.entity_id,
        "entity_name": entity.name,
        "url": candidate.url,
        "source_tier": candidate.source_tier,
        "same_domain_match": same_domain_match,
        "same_domain_bonus": same_domain_bonus,
        "pdf_bonus": pdf_bonus,
        "keyword_match": keyword_match,
        "keyword_bonus": keyword_bonus,
        "official_bonus": official_bonus,
        "final_confidence": round(candidate.confidence, 3),
    }
    return candidate, breakdown


def rank_candidate(entity: EntityRecord, candidate: DiscoveryCandidate, policy: ContextPolicy | None = None) -> DiscoveryCandidate:
    if policy is None:
        from doc_workbench.policy import load_context_policy

        policy = load_context_policy()
    ranked, _ = score_candidate(entity, candidate, policy)
    return ranked


def build_ranking_trace(records: list[DiscoveryRecord], policy: ContextPolicy, top_n: int = 5) -> list[dict[str, Any]]:
    ranking_rows: list[dict[str, Any]] = []
    for record in records:
        scored: list[dict[str, Any]] = []
        for candidate in record.candidates:
            _, breakdown = score_candidate(record.entity, candidate, policy)
            scored.append(breakdown)
        scored.sort(key=lambda item: float(item["final_confidence"]), reverse=True)
        ranking_rows.append(
            {
                "entity_id": record.entity.entity_id,
                "entity_name": record.entity.name,
                "ranked_candidates": scored[:top_n],
            }
        )
    return ranking_rows


def _top_candidate_fields(candidates: list[DiscoveryCandidate]) -> tuple[str, float]:
    if not candidates:
        return "", 0.0
    return candidates[0].url, float(candidates[0].confidence)


def _followup_allowed(
    *,
    policy: ContextPolicy,
    followup_search: bool,
    official_candidates: list[DiscoveryCandidate],
    regulatory_candidates: list[DiscoveryCandidate],
) -> tuple[bool, str]:
    if not followup_search and policy.followup_search.require_explicit_flag:
        return False, "cli_flag_disabled"
    if not policy.followup_search.skip_if_higher_priority_approved:
        return True, "enabled"
    approval_cutoff = policy.review_thresholds.approved_min_confidence
    for candidate in [*official_candidates, *regulatory_candidates]:
        if candidate.confidence >= approval_cutoff:
            return False, "higher_priority_candidate_already_approved"
    return True, "enabled"


async def _discover_official_site(entity: EntityRecord, policy: ContextPolicy, exec_policy: object = None) -> list[DiscoveryCandidate]:
    official_url = _normalize_official_url(entity.official_website)
    if not official_url:
        return []
    # Enforce domain before the first request and at every redirect hop.
    # safe_get uses follow_redirects=False and calls enforce_domain per hop.
    content_bytes, _ct, final_url = await safe_get(official_url, exec_policy=exec_policy, timeout=20.0)
    html_text = content_bytes.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html_text, "html.parser")
    candidates: list[DiscoveryCandidate] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(final_url, anchor["href"])
        title = " ".join(anchor.stripped_strings)
        if not _same_domain(href, official_url):
            continue
        if not KEYWORD_RE.search(f"{title} {href}"):
            continue
        candidate = DiscoveryCandidate(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            url=href,
            title=title,
            snippet="official site",
            source_type="official_site",
            source_tier="official",
            document_kind="official_html",
            year=_extract_year(f"{title} {href}"),
            confidence=0.0,
            reasons=["official_site_link"],
        )
        candidates.append(score_candidate(entity, candidate, policy)[0])
    deduped: dict[str, DiscoveryCandidate] = {}
    for candidate in candidates:
        existing = deduped.get(candidate.url)
        if existing is None or candidate.confidence > existing.confidence:
            deduped[candidate.url] = candidate
    return sorted(deduped.values(), key=lambda item: item.confidence, reverse=True)[:10]


async def _discover_search_results(entity: EntityRecord, policy: ContextPolicy, exec_policy: object = None) -> list[DiscoveryCandidate]:
    provider = get_search_provider()
    query = f"{entity.name} annual report pdf investor relations"
    results = await provider.search(query, max_results=5, exec_policy=exec_policy)
    candidates: list[DiscoveryCandidate] = []
    for result in results:
        tier = "search_same_domain" if _same_domain(result.url, entity.official_website) else "search_fallback"
        kind = "official_pdf" if result.url.lower().endswith(".pdf") and tier == "search_same_domain" else "other"
        candidate = DiscoveryCandidate(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            url=result.url,
            title=result.title,
            snippet=result.snippet,
            source_type="search",
            source_tier=tier,
            document_kind=kind,
            year=_extract_year(f"{result.title} {result.snippet} {result.url}"),
            confidence=0.0,
            reasons=["search_result"],
        )
        candidates.append(score_candidate(entity, candidate, policy)[0])
    return candidates


async def discover_entity(
    entity: EntityRecord,
    *,
    followup_search: bool = False,
    policy: ContextPolicy | None = None,
    tracer: RunTrace | None = None,
    _skip_ranking: bool = False,
    _force_skip_followup: bool = False,
    exec_policy: object = None,
) -> DiscoveryRecord:
    """Discover annual-report candidates for a single entity.

    Parameters
    ----------
    _skip_ranking:
        When ``True`` the final dedup/sort/cap-to-10 step is skipped and the
        full raw candidate pool is returned.  Use this in the LangGraph path
        so that ``followup_node`` and ``rank_node`` operate on the complete set
        rather than an already-truncated list.  Not part of the public API.
    _force_skip_followup:
        When ``True`` follow-up extraction is unconditionally skipped,
        regardless of ``followup_search`` or policy settings.  Use this in the
        LangGraph path where follow-up is owned exclusively by ``followup_node``
        and must not run inside ``discover_entity``.  Not part of the public API.
    """
    if policy is None:
        from doc_workbench.policy import load_context_policy

        policy = load_context_policy()

    entity_start = time.perf_counter()
    errors: list[str] = []

    official_start = time.perf_counter()
    try:
        official_candidates = await _discover_official_site(entity, policy, exec_policy=exec_policy)
    except PolicyViolationError:
        raise
    except Exception as exc:
        official_candidates = []
        errors.append(f"official_site:{type(exc).__name__}")
    if tracer is not None:
        top_url, top_confidence = _top_candidate_fields(official_candidates)
        tracer.add_span(
            entity_id=entity.entity_id,
            stage="official_site_lookup",
            provider="official_site",
            latency_ms=(time.perf_counter() - official_start) * 1000.0,
            candidate_count_in=0,
            candidate_count_out=len(official_candidates),
            top_candidate_url=top_url,
            top_confidence=top_confidence,
        )

    regulatory_start = time.perf_counter()
    try:
        regulatory_raw = await RegulatoryFilingsProvider().discover(entity, exec_policy=exec_policy)
        regulatory_candidates = [
            DiscoveryCandidate(
                entity_id=entity.entity_id,
                entity_name=entity.name,
                url=row["url"],
                title=row.get("title", ""),
                snippet=row.get("snippet", ""),
                source_type=row.get("source_type", "regulatory_filings"),
                source_tier=row.get("source_tier", "regulatory"),
                document_kind=row.get("document_kind", "regulatory_filing"),
                year=row.get("year"),
                confidence=float(row.get("confidence", 0.0)),
                reasons=list(row.get("reasons", [])),
            )
            for row in regulatory_raw
        ]
    except PolicyViolationError:
        raise
    except Exception as exc:
        regulatory_candidates = []
        errors.append(f"regulatory_filings:{type(exc).__name__}")
    if tracer is not None:
        top_url, top_confidence = _top_candidate_fields(regulatory_candidates)
        tracer.add_span(
            entity_id=entity.entity_id,
            stage="regulatory_fallback_lookup",
            provider="regulatory_filings",
            latency_ms=(time.perf_counter() - regulatory_start) * 1000.0,
            candidate_count_in=0,
            candidate_count_out=len(regulatory_candidates),
            top_candidate_url=top_url,
            top_confidence=top_confidence,
        )

    search_start = time.perf_counter()
    try:
        search_candidates = await _discover_search_results(entity, policy, exec_policy=exec_policy)
    except PolicyViolationError:
        raise
    except Exception as exc:
        search_candidates = []
        errors.append(f"search:{type(exc).__name__}")
    if tracer is not None:
        top_url, top_confidence = _top_candidate_fields(search_candidates)
        tracer.add_span(
            entity_id=entity.entity_id,
            stage="search_expansion",
            provider="search",
            latency_ms=(time.perf_counter() - search_start) * 1000.0,
            candidate_count_in=0,
            candidate_count_out=len(search_candidates),
            top_candidate_url=top_url,
            top_confidence=top_confidence,
        )

    followup_candidates: list[DiscoveryCandidate] = []
    if _force_skip_followup:
        followup_enabled, followup_reason = False, "graph_node_owns_followup"
    else:
        followup_enabled, followup_reason = _followup_allowed(
            policy=policy,
            followup_search=followup_search,
            official_candidates=official_candidates,
            regulatory_candidates=regulatory_candidates,
        )
    followup_start = time.perf_counter()
    if followup_enabled and search_candidates:
        try:
            _results, followup_candidates = await run_followup_for_candidates(
                entity,
                [
                    candidate
                    for candidate in search_candidates
                    if candidate.source_tier in policy.followup_search.allowed_seed_source_tiers
                ],
                materialize=False,
                registry=None,
                exec_policy=exec_policy,
            )
        except PolicyViolationError:
            raise
        except Exception as exc:
            errors.append(f"followup_search:{type(exc).__name__}: {exc}")
    if tracer is not None and not _force_skip_followup:
        # Suppress this span in the LangGraph path — followup_node owns the
        # authoritative followup_extraction span there.
        top_url, top_confidence = _top_candidate_fields(followup_candidates)
        tracer.add_span(
            entity_id=entity.entity_id,
            stage="followup_extraction",
            provider="followup_search",
            latency_ms=(time.perf_counter() - followup_start) * 1000.0,
            candidate_count_in=len(search_candidates),
            candidate_count_out=len(followup_candidates),
            top_candidate_url=top_url,
            top_confidence=top_confidence,
            details={"enabled": followup_enabled, "reason": followup_reason},
        )

    ranking_start = time.perf_counter()
    all_candidates = official_candidates + regulatory_candidates + search_candidates + followup_candidates
    deduped: dict[str, DiscoveryCandidate] = {}
    for candidate in all_candidates:
        existing = deduped.get(candidate.url)
        if existing is None or candidate.confidence > existing.confidence:
            deduped[candidate.url] = candidate
    if _skip_ranking:
        # Return the full deduplicated pool without sorting or capping so that
        # downstream stages (followup_node, rank_node) receive the complete set.
        candidates = list(deduped.values())
    else:
        candidates = sorted(deduped.values(), key=lambda item: item.confidence, reverse=True)[:10]
    if tracer is not None:
        if _skip_ranking:
            # In the LangGraph path, ranking is deferred to rank_node.
            # Emit a ``candidate_pool_assembled`` span (distinct from the final
            # ``candidate_ranking`` span emitted by rank_node) so local traces
            # have full coverage without double-counting the ranking stage.
            # The ``ranking_deferred`` flag lets trace consumers identify this
            # as a pre-rank diagnostic rather than the authoritative ranking event.
            top_url, top_confidence = (
                max(candidates, key=lambda c: c.confidence, default=None),
                max((c.confidence for c in candidates), default=0.0),
            )
            top_url = top_url.url if top_url else ""
            tracer.add_span(
                entity_id=entity.entity_id,
                stage="candidate_pool_assembled",
                provider="orchestrator",
                latency_ms=(time.perf_counter() - ranking_start) * 1000.0,
                candidate_count_in=len(all_candidates),
                candidate_count_out=len(candidates),
                top_candidate_url=top_url,
                top_confidence=top_confidence,
                details={"ranking_deferred": True, "note": "unsorted; rank_node owns final ranking and capping"},
            )
        else:
            top_url, top_confidence = _top_candidate_fields(candidates)
            tracer.add_span(
                entity_id=entity.entity_id,
                stage="candidate_ranking",
                provider="ranking_policy",
                latency_ms=(time.perf_counter() - ranking_start) * 1000.0,
                candidate_count_in=len(all_candidates),
                candidate_count_out=len(candidates),
                top_candidate_url=top_url,
                top_confidence=top_confidence,
            )
            tracer.add_span(
                entity_id=entity.entity_id,
                stage="discover_entity",
                provider="orchestrator",
                latency_ms=(time.perf_counter() - entity_start) * 1000.0,
                candidate_count_in=0,
                candidate_count_out=len(candidates),
                top_candidate_url=top_url,
                top_confidence=top_confidence,
            )

    status = "success" if candidates else "no_result"
    return DiscoveryRecord(entity=entity, status=status, candidates=candidates, errors=errors)


def write_discovery_artifacts(output_dir: Path, records: list[DiscoveryRecord]) -> tuple[Path, Path]:
    json_path = output_dir / "discover.json"
    csv_path = output_dir / "discover_summary.csv"
    json_path.write_text(
        json.dumps([record.to_dict() for record in records], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "entity_id",
            "name",
            "status",
            "candidate_count",
            "top_url",
            "top_source_tier",
            "top_confidence",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            top = record.candidates[0] if record.candidates else None
            writer.writerow(
                {
                    "entity_id": record.entity.entity_id,
                    "name": record.entity.name,
                    "status": record.status,
                    "candidate_count": len(record.candidates),
                    "top_url": top.url if top else "",
                    "top_source_tier": top.source_tier if top else "",
                    "top_confidence": top.confidence if top else "",
                }
            )
    return json_path, csv_path
