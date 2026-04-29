from __future__ import annotations

import hashlib
import importlib.resources
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


def _load_policy_yaml(policy_path: str | Path | None) -> tuple[dict[str, Any], str]:
    """Load and return the raw policy YAML payload and the resolved path string.

    Resolution order when *policy_path* is ``None``:
    1. ``importlib.resources`` — works after ``pip install`` including zip wheels.
    2. Repo-relative ``context/context_policy.yaml`` — fallback for un-installed
       source-tree runs (e.g. ``python doc_workbench/cli.py``).
    """
    if policy_path is not None:
        p = Path(policy_path)
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}, str(p)

    # Try package-data path via importlib.resources (zip-safe).
    try:
        ref = importlib.resources.files("doc_workbench.context").joinpath("context_policy.yaml")
        with importlib.resources.as_file(ref) as p:
            content = Path(p).read_text(encoding="utf-8")
            resolved = str(p)
        return yaml.safe_load(content) or {}, resolved
    except (ModuleNotFoundError, FileNotFoundError, TypeError):
        pass

    # Fallback: repo-relative path for direct source-tree runs.
    fallback = Path("context/context_policy.yaml")
    if fallback.exists():
        return yaml.safe_load(fallback.read_text(encoding="utf-8")) or {}, str(fallback)

    raise FileNotFoundError(
        "Cannot locate context_policy.yaml. "
        "Install the package with 'pip install -e .' or run from the repo root."
    )


@dataclass(slots=True)
class SameDomainPreference:
    enabled: bool
    score_bonus: float
    require_for_auto_approve: bool


@dataclass(slots=True)
class ReviewThresholds:
    approved_min_confidence: float
    needs_review_min_confidence: float


@dataclass(slots=True)
class FollowupPolicy:
    require_explicit_flag: bool
    skip_if_higher_priority_approved: bool
    allowed_seed_source_tiers: list[str]


@dataclass(slots=True)
class ContextPolicy:
    acquisition_order: list[str]
    preferred_candidate_kinds: list[str]
    same_domain_preference: SameDomainPreference
    review_thresholds: ReviewThresholds
    followup_search: FollowupPolicy
    policy_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_context_policy(policy_path: str | Path | None = None) -> ContextPolicy:
    payload, resolved_path_str = _load_policy_yaml(policy_path)
    acquisition_order = list(payload.get("acquisition_order") or [])
    if acquisition_order != ["official_site", "regulatory_filings", "search_expansion", "followup_extraction"]:
        raise ValueError("context_policy.yaml must define the supported acquisition order explicitly")

    same_domain_raw = payload.get("same_domain_preference") or {}
    thresholds_raw = payload.get("review_thresholds") or {}
    followup_raw = payload.get("followup_search") or {}

    approved_threshold = float(thresholds_raw.get("approved_min_confidence", 0.8))
    review_threshold = float(thresholds_raw.get("needs_review_min_confidence", 0.45))
    if not (0.0 <= review_threshold <= approved_threshold <= 1.0):
        raise ValueError("review thresholds must satisfy 0 <= needs_review <= approved <= 1")

    return ContextPolicy(
        acquisition_order=acquisition_order,
        preferred_candidate_kinds=list(payload.get("preferred_candidate_kinds") or []),
        same_domain_preference=SameDomainPreference(
            enabled=bool(same_domain_raw.get("enabled", True)),
            score_bonus=float(same_domain_raw.get("score_bonus", 0.35)),
            require_for_auto_approve=bool(same_domain_raw.get("require_for_auto_approve", False)),
        ),
        review_thresholds=ReviewThresholds(
            approved_min_confidence=approved_threshold,
            needs_review_min_confidence=review_threshold,
        ),
        followup_search=FollowupPolicy(
            require_explicit_flag=bool(followup_raw.get("require_explicit_flag", True)),
            skip_if_higher_priority_approved=bool(followup_raw.get("skip_if_higher_priority_approved", True)),
            allowed_seed_source_tiers=list(followup_raw.get("allowed_seed_source_tiers") or []),
        ),
        policy_path=resolved_path_str,
    )


def write_resolved_policy(path: Path, policy: ContextPolicy) -> Path:
    path.write_text(json.dumps(policy.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path
