from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {}, ())
    }


@dataclass(slots=True)
class EntityRecord:
    entity_id: str
    name: str
    ticker: str = ""
    official_website: str = ""
    cik: str = ""
    country: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class DiscoveryCandidate:
    entity_id: str
    entity_name: str
    url: str
    title: str = ""
    snippet: str = ""
    source_type: str = ""
    source_tier: str = ""
    document_kind: str = ""
    year: int | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    promotion_source: str = ""
    seed_url: str = ""
    followup_confidence: float | None = None
    followup_pointer_type: str = ""
    followup_seed_document_id: str = ""
    followup_target_document_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confidence"] = round(float(self.confidence), 3)
        if self.followup_confidence is not None:
            payload["followup_confidence"] = round(float(self.followup_confidence), 3)
        return _compact_dict(payload)


@dataclass(slots=True)
class DiscoveryRecord:
    entity: EntityRecord
    status: str
    candidates: list[DiscoveryCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.entity.to_dict(),
            "status": self.status,
            "errors": self.errors,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(slots=True)
class ReviewRow:
    entity_id: str
    entity_name: str
    url: str
    recommendation: str
    candidate_kind: str
    confidence: float
    needs_manual_review: bool
    review_notes: str = ""
    source_tier: str = ""
    year: int | None = None
    promotion_source: str = ""
    seed_url: str = ""
    followup_pointer_type: str = ""
    followup_seed_document_id: str = ""
    followup_target_document_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confidence"] = round(float(self.confidence), 3)
        return _compact_dict(payload)


@dataclass(slots=True)
class DownloadRow:
    document_id: str
    entity_id: str
    entity_name: str
    url: str
    local_path: str
    byte_size: int
    is_duplicate: bool
    status: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class MetadataScanRow:
    document_id: str
    entity_id: str
    entity_name: str
    title: str
    issuer_name: str
    reporting_period: str
    publication_date: str
    page_count: int | None
    modality: str
    status: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class RegistrationResult:
    document_id: str
    document_folder: Path
    local_path: Path
    is_duplicate: bool
