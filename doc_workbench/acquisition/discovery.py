from __future__ import annotations

import asyncio
import csv
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from doc_workbench.acquisition.followup.workflow import run_followup_for_candidates
from doc_workbench.models import DiscoveryCandidate, DiscoveryRecord, EntityRecord
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
    if not match:
        return None
    return int(match.group(1))


def _same_domain(url: str, official_website: str) -> bool:
    if not official_website:
        return False
    candidate_domain = urlparse(url).netloc.lower().removeprefix("www.")
    official_domain = urlparse(_normalize_official_url(official_website)).netloc.lower().removeprefix("www.")
    return bool(candidate_domain and official_domain and candidate_domain.endswith(official_domain))


def rank_candidate(entity: EntityRecord, candidate: DiscoveryCandidate) -> DiscoveryCandidate:
    score = 0.35
    reasons = list(candidate.reasons)
    if _same_domain(candidate.url, entity.official_website):
        score += 0.35
        reasons.append("same_domain")
    if candidate.url.lower().endswith(".pdf"):
        score += 0.15
        reasons.append("pdf")
    if KEYWORD_RE.search(f"{candidate.title} {candidate.url}"):
        score += 0.1
        reasons.append("annual_report_terms")
    if candidate.source_tier == "official":
        score += 0.05
    candidate.confidence = min(score, 0.99)
    candidate.reasons = sorted(set(reasons))
    if candidate.url.lower().endswith(".pdf"):
        candidate.document_kind = candidate.document_kind or "official_pdf"
    return candidate


async def _discover_official_site(entity: EntityRecord) -> list[DiscoveryCandidate]:
    official_url = _normalize_official_url(entity.official_website)
    if not official_url:
        return []
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(official_url)
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    candidates: list[DiscoveryCandidate] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(str(response.url), anchor["href"])
        title = " ".join(anchor.stripped_strings)
        if not _same_domain(href, official_url):
            continue
        if not KEYWORD_RE.search(f"{title} {href}"):
            continue
        candidates.append(
            DiscoveryCandidate(
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
        )
    deduped: dict[str, DiscoveryCandidate] = {}
    for candidate in candidates:
        ranked = rank_candidate(entity, candidate)
        existing = deduped.get(ranked.url)
        if existing is None or ranked.confidence > existing.confidence:
            deduped[ranked.url] = ranked
    return sorted(deduped.values(), key=lambda item: item.confidence, reverse=True)[:10]


async def _discover_search_results(entity: EntityRecord) -> list[DiscoveryCandidate]:
    provider = get_search_provider()
    query = f"{entity.name} annual report pdf investor relations"
    results = await provider.search(query, max_results=5)
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
        candidates.append(rank_candidate(entity, candidate))
    return candidates


async def discover_entity(
    entity: EntityRecord,
    *,
    followup_search: bool = False,
) -> DiscoveryRecord:
    errors: list[str] = []
    tasks = [
        _discover_official_site(entity),
        RegulatoryFilingsProvider().discover(entity),
        _discover_search_results(entity),
    ]
    official_candidates: list[DiscoveryCandidate] = []
    regulatory_candidates: list[DiscoveryCandidate] = []
    search_candidates: list[DiscoveryCandidate] = []
    results = await asyncio.gather(*tasks, return_exceptions=True)

    if isinstance(results[0], Exception):
        errors.append(f"official_site:{type(results[0]).__name__}")
    else:
        official_candidates = results[0]

    if isinstance(results[1], Exception):
        errors.append(f"regulatory_filings:{type(results[1]).__name__}")
    else:
        for raw in results[1]:
            regulatory_candidates.append(
                DiscoveryCandidate(
                    entity_id=entity.entity_id,
                    entity_name=entity.name,
                    url=raw["url"],
                    title=raw.get("title", ""),
                    snippet=raw.get("snippet", ""),
                    source_type=raw.get("source_type", "regulatory_filings"),
                    source_tier=raw.get("source_tier", "regulatory"),
                    document_kind=raw.get("document_kind", "regulatory_filing"),
                    year=raw.get("year"),
                    confidence=float(raw.get("confidence", 0.0)),
                    reasons=list(raw.get("reasons", [])),
                )
            )

    if isinstance(results[2], Exception):
        errors.append(f"search:{type(results[2]).__name__}")
    else:
        search_candidates = results[2]

    followup_candidates: list[DiscoveryCandidate] = []
    if followup_search and search_candidates:
        try:
            _results, followup_candidates = await run_followup_for_candidates(
                entity,
                search_candidates,
                materialize=False,
                registry=None,
            )
        except Exception as exc:
            errors.append(f"followup_search:{type(exc).__name__}")

    all_candidates = official_candidates + regulatory_candidates + search_candidates + followup_candidates
    deduped: dict[str, DiscoveryCandidate] = {}
    for candidate in all_candidates:
        existing = deduped.get(candidate.url)
        if existing is None or candidate.confidence > existing.confidence:
            deduped[candidate.url] = candidate
    candidates = sorted(deduped.values(), key=lambda item: item.confidence, reverse=True)[:10]
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
