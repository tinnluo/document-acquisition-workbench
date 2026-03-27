from __future__ import annotations

from doc_workbench.models import DiscoveryCandidate, ReviewRow


def classify_candidate(candidate: DiscoveryCandidate) -> tuple[str, str, bool]:
    url = candidate.url.lower()
    if candidate.source_tier == "official" and url.endswith(".pdf"):
        return "approved", "official_pdf", False
    if candidate.source_tier == "followup_same_domain" and url.endswith(".pdf"):
        return "approved", "official_pdf", False
    if candidate.source_tier == "official":
        return "needs_review", "official_html", True
    if candidate.source_tier == "regulatory":
        return "approved", "regulatory_filing", False
    if candidate.source_tier == "search_same_domain" and url.endswith(".pdf"):
        return "needs_review", "official_pdf", True
    if url.endswith(".pdf"):
        return "needs_review", "third_party_pdf", True
    return "rejected", "other", False


def to_review_row(candidate: DiscoveryCandidate) -> ReviewRow:
    recommendation, candidate_kind, needs_manual_review = classify_candidate(candidate)
    notes = ",".join(candidate.reasons)
    return ReviewRow(
        entity_id=candidate.entity_id,
        entity_name=candidate.entity_name,
        url=candidate.url,
        recommendation=recommendation,
        candidate_kind=candidate_kind,
        confidence=candidate.confidence,
        needs_manual_review=needs_manual_review,
        review_notes=notes,
        source_tier=candidate.source_tier,
        year=candidate.year,
        promotion_source=candidate.promotion_source,
        seed_url=candidate.seed_url,
        followup_pointer_type=candidate.followup_pointer_type,
        followup_seed_document_id=candidate.followup_seed_document_id,
        followup_target_document_id=candidate.followup_target_document_id,
    )
