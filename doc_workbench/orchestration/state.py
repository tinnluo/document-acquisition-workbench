"""Shared state object for the LangGraph acquisition workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from doc_workbench.models import DiscoveryRecord, ReviewRow
from doc_workbench.observability.tracer import RunTrace
from doc_workbench.policy import ContextPolicy


class WorkbenchState(TypedDict, total=False):
    """Shared mutable state threaded through all LangGraph nodes.

    Fields are populated progressively as the graph executes:
      discover_node      → discovery_records
      followup_node      → followup_records (enriched discovery_records)
      rank_node          → ranked_records
      review_prep_node   → review_rows, recommendation_summary, review_trace
    """

    # --- inputs (set before graph invocation) ---
    entities: list[Any]           # list[EntityRecord]
    policy: ContextPolicy
    tracer: RunTrace
    output_dir: Path
    followup_search: bool

    # --- stage outputs ---
    discovery_records: list[DiscoveryRecord]
    followup_records: list[DiscoveryRecord]   # discovery_records enriched with followup candidates
    ranked_records: list[DiscoveryRecord]     # followup_records after dedup + rank + cap

    review_rows: list[ReviewRow]
    review_trace: list[dict[str, Any]]
    recommendation_summary: dict[str, int]
