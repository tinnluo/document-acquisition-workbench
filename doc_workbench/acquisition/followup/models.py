from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class FollowupPointer:
    url: str
    pointer_type: str
    source_url: str
    anchor_text: str = ""
    context_text: str = ""
    extraction_method: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in ("", None, [], {})
        }


@dataclass(slots=True)
class ResolvedTarget:
    original_url: str
    resolved_url: str
    final_url: str
    content_type: str = ""
    status_code: int = 0
    is_accessible: bool = False
    target_type: str = ""
    classification_confidence: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    matched_patterns: list[str] = field(default_factory=list)
    pointer: FollowupPointer | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["classification_confidence"] = round(float(self.classification_confidence), 3)
        if self.pointer is not None:
            payload["pointer"] = self.pointer.to_dict()
        return {
            key: value
            for key, value in payload.items()
            if value not in ("", None, [], {})
        }


@dataclass(slots=True)
class FollowupResult:
    seed_url: str
    source_type: str
    pointer_count: int
    promoted_count: int
    pointers: list[FollowupPointer] = field(default_factory=list)
    resolved_targets: list[ResolvedTarget] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    seed_document_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_url": self.seed_url,
            "source_type": self.source_type,
            "pointer_count": self.pointer_count,
            "promoted_count": self.promoted_count,
            "seed_document_id": self.seed_document_id,
            "errors": self.errors,
            "pointers": [pointer.to_dict() for pointer in self.pointers],
            "resolved_targets": [target.to_dict() for target in self.resolved_targets],
        }
