from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from doc_workbench.models import DiscoveryCandidate, ReviewRow
from doc_workbench.policy import ContextPolicy
from doc_workbench.review.classifier import to_review_row


def build_review_rows(
    discovery_json: Path,
    policy: ContextPolicy,
) -> tuple[list[ReviewRow], list[dict[str, Any]], dict[str, int]]:
    payload = json.loads(discovery_json.read_text(encoding="utf-8"))
    rows: list[ReviewRow] = []
    review_trace: list[dict[str, Any]] = []
    recommendation_summary = {"approved": 0, "needs_review": 0, "rejected": 0}

    for record in payload:
        for raw in record.get("candidates", []):
            candidate = DiscoveryCandidate(
                entity_id=str(raw.get("entity_id") or record.get("entity_id") or ""),
                entity_name=str(raw.get("entity_name") or record.get("name") or ""),
                url=str(raw.get("url") or ""),
                title=str(raw.get("title") or ""),
                snippet=str(raw.get("snippet") or ""),
                source_type=str(raw.get("source_type") or ""),
                source_tier=str(raw.get("source_tier") or ""),
                document_kind=str(raw.get("document_kind") or ""),
                year=raw.get("year"),
                confidence=float(raw.get("confidence") or 0.0),
                reasons=list(raw.get("reasons") or []),
                promotion_source=str(raw.get("promotion_source") or ""),
                seed_url=str(raw.get("seed_url") or ""),
                followup_confidence=float(raw["followup_confidence"]) if raw.get("followup_confidence") is not None else None,
                followup_pointer_type=str(raw.get("followup_pointer_type") or ""),
                followup_seed_document_id=str(raw.get("followup_seed_document_id") or ""),
                followup_target_document_id=str(raw.get("followup_target_document_id") or ""),
            )
            row, trace_row = to_review_row(candidate, policy)
            rows.append(row)
            review_trace.append(trace_row)
            recommendation_summary[row.recommendation] = recommendation_summary.get(row.recommendation, 0) + 1

    return rows, review_trace, recommendation_summary


def write_review_csv(path: Path, rows: list[ReviewRow]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "entity_id",
            "entity_name",
            "url",
            "recommendation",
            "candidate_kind",
            "confidence",
            "needs_manual_review",
            "review_notes",
            "source_tier",
            "year",
            "promotion_source",
            "seed_url",
            "followup_pointer_type",
            "followup_seed_document_id",
            "followup_target_document_id",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_dict())
    return path
