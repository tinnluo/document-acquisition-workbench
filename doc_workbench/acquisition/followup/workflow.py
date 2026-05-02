from __future__ import annotations

import asyncio
import csv
import json
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

# PolicyViolationError is imported at module level so every except clause can
# reference it without a local import.  It must never be swallowed.
from doc_workbench.execution_policy import PolicyViolationError

from bs4 import BeautifulSoup
from pypdf import PdfReader

from doc_workbench.acquisition.followup.classifier import classify_target
from doc_workbench.acquisition.followup.html_extractor import extract_html_pointers
from doc_workbench.acquisition.followup.models import FollowupResult
from doc_workbench.acquisition.followup.pdf_extractor import extract_pdf_pointers
from doc_workbench.acquisition.followup.resolver import resolve_pointer
from doc_workbench.http_utils import safe_get
from doc_workbench.models import DiscoveryCandidate, DiscoveryRecord, EntityRecord
from doc_workbench.registry.document_registry import DocumentRegistry

# ExecutionPolicy is imported lazily inside functions that need it to avoid
# circular imports and to keep the module importable without the policy layer.


def _normalize_url(url: str) -> str:
    return str(url or "").strip()


def _same_domain(url: str, official_website: str) -> bool:
    if not official_website:
        return False
    candidate_domain = urlparse(url).netloc.lower().removeprefix("www.")
    official_domain = urlparse(official_website if "://" in official_website else f"https://{official_website}").netloc.lower().removeprefix("www.")
    return bool(candidate_domain and official_domain and candidate_domain.endswith(official_domain))


def _pick_extension(content_type: str, url: str) -> str:
    lowered = content_type.lower()
    if "pdf" in lowered or url.lower().endswith(".pdf"):
        return ".pdf"
    if "html" in lowered or url.lower().endswith((".html", ".htm")):
        return ".html"
    return ".bin"


def _parse_content(content_bytes: bytes, content_type: str) -> dict[str, Any]:
    lowered = content_type.lower()
    if "pdf" in lowered:
        try:
            reader = PdfReader(BytesIO(content_bytes))
            metadata = reader.metadata or {}
            return {
                "title": str(metadata.get("/Title") or "").strip(),
                "page_count": len(reader.pages),
                "pointer_extractable": True,
            }
        except Exception as exc:
            return {"parse_error": f"{type(exc).__name__}: {exc}"}

    try:
        text = content_bytes.decode("utf-8", errors="ignore")
        soup = BeautifulSoup(text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""
        snippet = soup.get_text(" ", strip=True)[:200]
        return {"title": title, "snippet": snippet}
    except Exception as exc:
        return {"parse_error": f"{type(exc).__name__}: {exc}"}


async def _fetch_url(url: str, exec_policy: Any = None) -> tuple[bytes, str, str]:
    """Fetch *url* with per-hop domain enforcement via :func:`~doc_workbench.http_utils.safe_get`."""
    return await safe_get(_normalize_url(url), exec_policy=exec_policy, timeout=30.0)


def _extract_pointers(content_bytes: bytes, content_type: str, source_url: str) -> tuple[str, list]:
    lowered = content_type.lower()
    if "pdf" in lowered or source_url.lower().endswith(".pdf"):
        return "pdf", extract_pdf_pointers(content_bytes, source_url)
    try:
        html = content_bytes.decode("utf-8", errors="ignore")
    except Exception:
        html = ""
    return "html", extract_html_pointers(html, source_url)


async def _materialize_target(
    registry: DocumentRegistry,
    *,
    entity: EntityRecord,
    target_url: str,
    year: str,
    source_parent_document_id: str,
    exec_policy: Any = None,
) -> tuple[str, dict[str, Any]]:
    # Enforce download.enabled BEFORE issuing any network request so that a
    # disabled download policy always blocks at the entrypoint, even if a
    # higher-level guard is bypassed or regressed.
    if exec_policy is not None:
        from doc_workbench.execution_policy import enforce_download_enabled
        enforce_download_enabled(exec_policy)
    # Domain enforcement (including per-hop redirect validation) is handled
    # inside _fetch_url → safe_get.  Additional policy checks run after fetch.
    content_bytes, content_type, final_url = await _fetch_url(target_url, exec_policy=exec_policy)
    if exec_policy is not None:
        from doc_workbench.execution_policy import enforce_file_size, enforce_mime_type
        enforce_file_size(exec_policy, len(content_bytes), final_url)
        enforce_mime_type(exec_policy, content_type, final_url)
    parsed = _parse_content(content_bytes, content_type)
    registration = registry.register_artifact(
        entity_id=entity.entity_id,
        entity_name=entity.name,
        source_url=final_url,
        artifact_family="followup_targets",
        artifact_type="target_pdf" if "pdf" in content_type.lower() else "target_html",
        year=year,
        content_bytes=content_bytes,
        extension=_pick_extension(content_type, final_url),
        content_type=content_type or "application/octet-stream",
        stage="pre_review",
        source_parent_document_id=source_parent_document_id,
        parsed=parsed,
        dedupe_scope="family",
    )
    return registration.document_id, parsed


async def run_followup_for_candidates(
    entity: EntityRecord,
    seed_candidates: list[DiscoveryCandidate],
    *,
    materialize: bool,
    registry: DocumentRegistry | None = None,
    exec_policy: Any = None,
) -> tuple[list[FollowupResult], list[DiscoveryCandidate]]:
    results: list[FollowupResult] = []
    promoted: list[DiscoveryCandidate] = []
    # materialize_count: tracks unique artifact registrations (used as the
    # authoritative counter for download.max_count enforcement).
    materialize_count: int = 0
    # fetch_count: tracks every outbound GET/HEAD attempt regardless of dedupe
    # outcome so duplicate seeds cannot silently exceed the egress budget.
    fetch_count: int = 0

    for seed in seed_candidates:
        errors: list[str] = []
        seed_document_id = ""
        try:
            # Enforce download policy BEFORE any network call so that a
            # disabled or capped runtime never issues a seed GET.
            if exec_policy is not None:
                from doc_workbench.execution_policy import (
                    enforce_download_enabled,
                    enforce_download_count,
                )
                enforce_download_enabled(exec_policy)
                # Check against fetch_count (not just unique materializations) so
                # repeated/duplicate inputs cannot silently exceed the egress budget.
                enforce_download_count(exec_policy, fetch_count)
            fetch_count += 1
            # Domain enforcement (including per-hop redirect validation) is
            # handled inside _fetch_url → safe_get.
            content_bytes, content_type, final_seed_url = await _fetch_url(seed.url, exec_policy=exec_policy)
            if exec_policy is not None:
                from doc_workbench.execution_policy import enforce_file_size, enforce_mime_type
                enforce_file_size(exec_policy, len(content_bytes), final_seed_url)
                enforce_mime_type(exec_policy, content_type, final_seed_url)
        except PolicyViolationError:
            # Policy blocks are deterministic — propagate immediately so the
            # caller aborts rather than silently continuing with other seeds.
            raise
        except Exception as exc:
            results.append(
                FollowupResult(
                    seed_url=seed.url,
                    source_type="unknown",
                    pointer_count=0,
                    promoted_count=0,
                    errors=[f"{type(exc).__name__}: {exc}"],
                )
            )
            continue

        source_type, pointers = _extract_pointers(content_bytes, content_type, final_seed_url)

        # Filter pointers to allowed domains before any network I/O.
        # Relative URLs (e.g. "/reports/2024.pdf") must be resolved against
        # the seed URL first — they have no hostname so enforce_domain would
        # incorrectly reject them even when they resolve to an allowed host.
        if exec_policy is not None:
            from doc_workbench.execution_policy import enforce_domain, PolicyViolationError as _PVE
            allowed_pointers = []
            for pointer in pointers:
                abs_url = (
                    pointer.url
                    if pointer.url.startswith(("http://", "https://"))
                    else urljoin(final_seed_url, pointer.url)
                )
                try:
                    enforce_domain(exec_policy, abs_url)
                    allowed_pointers.append(pointer)
                except _PVE as exc:
                    errors.append(f"policy:domain_blocked: {exc}")
            pointers = allowed_pointers

        seed_parse = _parse_content(content_bytes, content_type)
        if materialize and registry is not None:
            seed_registration = registry.register_artifact(
                entity_id=entity.entity_id,
                entity_name=entity.name,
                source_url=final_seed_url,
                artifact_family="search_surfaces",
                artifact_type="seed_pdf" if source_type == "pdf" else "seed_html",
                year=str(seed.year or "unknown"),
                content_bytes=content_bytes,
                extension=_pick_extension(content_type, final_seed_url),
                content_type=content_type or "application/octet-stream",
                stage="pre_review",
                parsed={**seed_parse, "pointer_count": len(pointers)},
                dedupe_scope="family",
            )
            seed_document_id = seed_registration.document_id
            if not seed_registration.is_duplicate:
                materialize_count += 1

        resolved = await asyncio.gather(
            *(resolve_pointer(pointer, exec_policy=exec_policy) for pointer in pointers),
            return_exceptions=True,
        )
        classified = []
        for item in resolved:
            if isinstance(item, PolicyViolationError):
                # Policy blocks from resolve_pointer must not be swallowed —
                # re-raise immediately so the caller can abort the run.
                raise item
            if isinstance(item, Exception):
                errors.append(f"resolve:{type(item).__name__}: {item}")
                continue
            # Per-hop domain enforcement is now inside resolve_pointer → safe_head,
            # so no post-hoc re-validation needed here.
            target = classify_target(item)
            if target.is_accessible:
                classified.append(target)
        classified.sort(key=lambda item: item.classification_confidence, reverse=True)

        promoted_count = 0
        for target in classified:
            if target.classification_confidence < 0.4 or not target.target_type:
                continue
            # Enforce max_count against fetch_count (every outbound request)
            # before any registry write, so duplicates cannot bypass the cap.
            if exec_policy is not None:
                from doc_workbench.execution_policy import enforce_download_count
                # PolicyViolationError here means the cap is exhausted — re-raise
                # to abort all further target processing for this seed.
                enforce_download_count(exec_policy, fetch_count)
            followup_target_document_id = ""
            if materialize and registry is not None:
                try:
                    fetch_count += 1
                    followup_target_document_id, _parsed = await _materialize_target(
                        registry,
                        entity=entity,
                        target_url=target.final_url,
                        year=str(seed.year or "unknown"),
                        source_parent_document_id=seed_document_id,
                        exec_policy=exec_policy,
                    )
                    materialize_count += 1
                except PolicyViolationError:
                    raise
                except Exception as exc:
                    errors.append(f"materialize_target:{type(exc).__name__}: {exc} url={target.final_url}")
                    continue

            promoted.append(
                DiscoveryCandidate(
                    entity_id=entity.entity_id,
                    entity_name=entity.name,
                    url=target.final_url,
                    title=target.pointer.anchor_text if target.pointer else seed.title,
                    snippet=target.pointer.context_text if target.pointer else seed.snippet,
                    source_type="followup_search",
                    source_tier="followup_same_domain" if _same_domain(target.final_url, entity.official_website) else "followup_search",
                    document_kind="official_pdf" if "pdf" in target.content_type.lower() else "official_html",
                    year=seed.year,
                    confidence=target.classification_confidence,
                    reasons=sorted(set(["followup_promoted", *target.matched_keywords])),
                    promotion_source="followup_search",
                    seed_url=seed.url,
                    followup_confidence=target.classification_confidence,
                    followup_pointer_type=target.pointer.pointer_type if target.pointer else "",
                    followup_seed_document_id=seed_document_id,
                    followup_target_document_id=followup_target_document_id,
                )
            )
            promoted_count += 1
            if promoted_count >= 2:
                break

        results.append(
            FollowupResult(
                seed_url=seed.url,
                source_type=source_type,
                pointer_count=len(pointers),
                promoted_count=promoted_count,
                pointers=pointers,
                resolved_targets=classified[:5],
                errors=errors,
                seed_document_id=seed_document_id,
            )
        )

    deduped: dict[str, DiscoveryCandidate] = {}
    for candidate in promoted:
        existing = deduped.get(candidate.url)
        if existing is None or candidate.confidence > existing.confidence:
            deduped[candidate.url] = candidate
    return results, sorted(deduped.values(), key=lambda item: item.confidence, reverse=True)


def load_discovery_records(path: Path) -> list[DiscoveryRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records: list[DiscoveryRecord] = []
    for raw in payload:
        entity = EntityRecord(
            entity_id=str(raw.get("entity_id") or ""),
            name=str(raw.get("name") or ""),
            ticker=str(raw.get("ticker") or ""),
            official_website=str(raw.get("official_website") or ""),
            cik=str(raw.get("cik") or ""),
            country=str(raw.get("country") or ""),
        )
        candidates = [
            DiscoveryCandidate(
                entity_id=str(candidate.get("entity_id") or entity.entity_id),
                entity_name=str(candidate.get("entity_name") or entity.name),
                url=str(candidate.get("url") or ""),
                title=str(candidate.get("title") or ""),
                snippet=str(candidate.get("snippet") or ""),
                source_type=str(candidate.get("source_type") or ""),
                source_tier=str(candidate.get("source_tier") or ""),
                document_kind=str(candidate.get("document_kind") or ""),
                year=candidate.get("year"),
                confidence=float(candidate.get("confidence") or 0.0),
                reasons=list(candidate.get("reasons") or []),
                promotion_source=str(candidate.get("promotion_source") or ""),
                seed_url=str(candidate.get("seed_url") or ""),
                followup_confidence=float(candidate["followup_confidence"]) if candidate.get("followup_confidence") is not None else None,
                followup_pointer_type=str(candidate.get("followup_pointer_type") or ""),
                followup_seed_document_id=str(candidate.get("followup_seed_document_id") or ""),
                followup_target_document_id=str(candidate.get("followup_target_document_id") or ""),
            )
            for candidate in raw.get("candidates", [])
        ]
        records.append(
            DiscoveryRecord(
                entity=entity,
                status=str(raw.get("status") or "no_result"),
                candidates=candidates,
                errors=list(raw.get("errors") or []),
            )
        )
    return records


def write_followup_artifacts(
    output_dir: Path,
    *,
    results_by_entity: dict[str, list[FollowupResult]],
    promoted_candidates: list[DiscoveryCandidate],
    enriched_records: list[DiscoveryRecord],
) -> tuple[Path, Path, Path]:
    results_path = output_dir / "followup_search_results.json"
    promoted_json_path = output_dir / "followup_promoted_candidates.json"
    enriched_path = output_dir / "discover_enriched.json"
    results_path.write_text(
        json.dumps(
            {
                entity_id: [result.to_dict() for result in entity_results]
                for entity_id, entity_results in results_by_entity.items()
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    promoted_json_path.write_text(
        json.dumps([candidate.to_dict() for candidate in promoted_candidates], indent=2),
        encoding="utf-8",
    )
    enriched_path.write_text(
        json.dumps([record.to_dict() for record in enriched_records], indent=2),
        encoding="utf-8",
    )
    promoted_csv_path = output_dir / "followup_promoted_candidates.csv"
    with promoted_csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "entity_id",
            "entity_name",
            "url",
            "source_tier",
            "document_kind",
            "confidence",
            "promotion_source",
            "seed_url",
            "followup_pointer_type",
            "followup_seed_document_id",
            "followup_target_document_id",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in promoted_candidates:
            payload = candidate.to_dict()
            writer.writerow({field: payload.get(field, "") for field in fieldnames})
    return results_path, promoted_json_path, enriched_path
