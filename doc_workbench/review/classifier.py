from __future__ import annotations

from typing import Any

from doc_workbench.models import DiscoveryCandidate, ReviewRow
from doc_workbench.policy import ContextPolicy, load_context_policy


def _confidence_band(candidate: DiscoveryCandidate, policy: ContextPolicy) -> str:
    if candidate.confidence >= policy.review_thresholds.approved_min_confidence:
        return "approved_band"
    if candidate.confidence >= policy.review_thresholds.needs_review_min_confidence:
        return "review_band"
    return "rejected_band"


def _classify_with_policy(
    candidate: DiscoveryCandidate,
    policy: ContextPolicy,
) -> tuple[str, str, bool, list[str], str]:
    url = candidate.url.lower()
    band = _confidence_band(candidate, policy)
    reason_codes = [f"source_tier:{candidate.source_tier}", f"confidence_band:{band}", *candidate.reasons]

    auto_approve_sources = {
        ("official", True): "official_pdf",
        ("followup_same_domain", True): "official_pdf",
        ("regulatory", False): "regulatory_filing",
        ("regulatory", True): "regulatory_filing",
    }
    is_pdf = url.endswith(".pdf")
    auto_kind = auto_approve_sources.get((candidate.source_tier, is_pdf))

    if auto_kind and candidate.confidence >= policy.review_thresholds.approved_min_confidence:
        if (
            policy.same_domain_preference.require_for_auto_approve
            and candidate.source_tier in {"official", "followup_same_domain"}
            and "same_domain" not in candidate.reasons
        ):
            return "needs_review", auto_kind, True, reason_codes + ["same_domain_required_for_auto_approve"], band
        return "approved", auto_kind, False, reason_codes, band

    if candidate.source_tier == "official":
        return "needs_review", "official_html", True, reason_codes, band
    if candidate.source_tier == "search_same_domain" and is_pdf and candidate.confidence >= policy.review_thresholds.needs_review_min_confidence:
        return "needs_review", "official_pdf", True, reason_codes, band
    if is_pdf and candidate.confidence >= policy.review_thresholds.needs_review_min_confidence:
        return "needs_review", "third_party_pdf", True, reason_codes, band
    return "rejected", "other", False, reason_codes, band


def classify_candidate(
    candidate: DiscoveryCandidate,
    policy: ContextPolicy | None = None,
) -> tuple[str, str, bool]:
    if policy is None:
        policy = load_context_policy()
    recommendation, candidate_kind, needs_manual_review, _reason_codes, _confidence_band = _classify_with_policy(candidate, policy)
    return recommendation, candidate_kind, needs_manual_review


def to_review_row(candidate: DiscoveryCandidate, policy: ContextPolicy | None = None) -> tuple[ReviewRow, dict[str, Any]]:
    if policy is None:
        policy = load_context_policy()
    recommendation, candidate_kind, needs_manual_review, reason_codes, confidence_band = _classify_with_policy(candidate, policy)
    row = ReviewRow(
        entity_id=candidate.entity_id,
        entity_name=candidate.entity_name,
        url=candidate.url,
        recommendation=recommendation,
        candidate_kind=candidate_kind,
        confidence=candidate.confidence,
        needs_manual_review=needs_manual_review,
        review_notes=",".join(reason_codes),
        source_tier=candidate.source_tier,
        year=candidate.year,
        promotion_source=candidate.promotion_source,
        seed_url=candidate.seed_url,
        followup_pointer_type=candidate.followup_pointer_type,
        followup_seed_document_id=candidate.followup_seed_document_id,
        followup_target_document_id=candidate.followup_target_document_id,
    )
    trace_row = {
        "entity_id": candidate.entity_id,
        "entity_name": candidate.entity_name,
        "candidate_url": candidate.url,
        "confidence": round(candidate.confidence, 3),
        "confidence_band": confidence_band,
        "reason_codes": reason_codes,
        "final_recommendation": recommendation,
    }
    return row, trace_row
