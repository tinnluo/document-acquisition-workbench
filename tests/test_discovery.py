from __future__ import annotations

from doc_workbench.acquisition.discovery import rank_candidate
from doc_workbench.models import DiscoveryCandidate, EntityRecord
from doc_workbench.review.classifier import classify_candidate


def test_rank_candidate_prioritizes_official_pdf() -> None:
    entity = EntityRecord(entity_id="1001", name="Example Corp", official_website="https://example.com")
    candidate = DiscoveryCandidate(
        entity_id="1001",
        entity_name="Example Corp",
        url="https://example.com/investors/annual-report-2024.pdf",
        title="Annual Report 2024",
        source_tier="official",
        source_type="official_site",
        document_kind="official_pdf",
        confidence=0.0,
    )
    ranked = rank_candidate(entity, candidate)
    assert ranked.confidence >= 0.8
    assert "same_domain" in ranked.reasons


def test_classifier_approves_regulatory_filing() -> None:
    candidate = DiscoveryCandidate(
        entity_id="1001",
        entity_name="Example Corp",
        url="https://www.sec.gov/example",
        source_tier="regulatory",
        confidence=0.8,
    )
    recommendation, candidate_kind, needs_manual_review = classify_candidate(candidate)
    assert recommendation == "approved"
    assert candidate_kind == "regulatory_filing"
    assert needs_manual_review is False
