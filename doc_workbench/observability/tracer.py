from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TraceSpan:
    """A single stage span within a pipeline run trace.

    Fields
    ------
    trace_id, entity_id, stage, provider
        Identity and routing metadata.
    latency_ms
        Wall-clock time for this stage in milliseconds.
    candidate_count_in / candidate_count_out
        How many document candidates entered and exited this stage.
    top_candidate_url, top_confidence
        Best-scored result after this stage.
    recommendation_summary
        ``{approved: N, needs_review: N, rejected: N}`` — populated only on
        the ``review_queue_generation`` stage.
    details
        Stage-specific free-form dict (e.g. ``{"enabled": false, "reason":
        "higher_priority_approved"}``).
    retry_count
        Number of retry attempts made before this span completed.  Currently
        always ``0``; reserved for future retry/backoff instrumentation where
        nodes catch transient errors (e.g. HTTP 429, network timeout) and
        retry before surfacing a result.  When retry logic is added, nodes
        should increment this counter on each failed attempt and pass the
        final value to ``RunTrace.add_span()``.
    """

    trace_id: str
    entity_id: str
    stage: str
    provider: str
    latency_ms: float
    candidate_count_in: int
    candidate_count_out: int
    top_candidate_url: str = ""
    top_confidence: float = 0.0
    recommendation_summary: dict[str, int] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["latency_ms"] = round(float(self.latency_ms), 3)
        payload["top_confidence"] = round(float(self.top_confidence), 3)
        return {
            key: value
            for key, value in payload.items()
            if value not in ("", None, [], {}, ())
        }


@dataclass(slots=True)
class RunTrace:
    trace_id: str
    run_id: str
    command: str
    policy_digest: str
    exec_policy_digest: str = ""
    spans: list[TraceSpan] = field(default_factory=list)

    def add_span(
        self,
        *,
        entity_id: str,
        stage: str,
        provider: str,
        latency_ms: float,
        candidate_count_in: int,
        candidate_count_out: int,
        top_candidate_url: str = "",
        top_confidence: float = 0.0,
        recommendation_summary: dict[str, int] | None = None,
        details: dict[str, Any] | None = None,
        retry_count: int = 0,
    ) -> None:
        self.spans.append(
            TraceSpan(
                trace_id=self.trace_id,
                entity_id=entity_id,
                stage=stage,
                provider=provider,
                latency_ms=latency_ms,
                candidate_count_in=candidate_count_in,
                candidate_count_out=candidate_count_out,
                top_candidate_url=top_candidate_url,
                top_confidence=top_confidence,
                recommendation_summary=recommendation_summary or {},
                details=details or {},
                retry_count=retry_count,
            )
        )

    def write(self, path: Path) -> Path:
        payload: dict[str, Any] = {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "command": self.command,
            "policy_digest": self.policy_digest,
            "spans": [span.to_dict() for span in self.spans],
        }
        if self.exec_policy_digest:
            payload["exec_policy_digest"] = self.exec_policy_digest
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path


def summarize_trace(trace_path: Path) -> dict[str, Any]:
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    spans = list(payload.get("spans") or [])
    by_stage: dict[str, int] = {}
    recommendation_summary: dict[str, int] = {}
    total_latency_ms = 0.0
    for span in spans:
        stage = str(span.get("stage") or "unknown")
        by_stage[stage] = by_stage.get(stage, 0) + 1
        total_latency_ms += float(span.get("latency_ms") or 0.0)
        for key, value in dict(span.get("recommendation_summary") or {}).items():
            recommendation_summary[key] = recommendation_summary.get(key, 0) + int(value)
    return {
        "trace_id": payload.get("trace_id"),
        "run_id": payload.get("run_id"),
        "command": payload.get("command"),
        "policy_digest": payload.get("policy_digest"),
        "stage_counts": by_stage,
        "recommendation_summary": recommendation_summary,
        "total_latency_ms": round(total_latency_ms, 3),
        "span_count": len(spans),
    }
