from __future__ import annotations

from doc_workbench.acquisition.followup.models import ResolvedTarget

POSITIVE_KEYWORDS = (
    "annual report",
    "annual-report",
    "10-k",
    "20-f",
    "financial statements",
    "investor",
)
NEGATIVE_KEYWORDS = (
    "proxy",
    "presentation",
    "earnings-call",
    "press release",
)


def classify_target(target: ResolvedTarget) -> ResolvedTarget:
    combined = " ".join(
        part
        for part in [
            target.final_url.lower(),
            (target.pointer.anchor_text.lower() if target.pointer and target.pointer.anchor_text else ""),
            (target.pointer.context_text.lower() if target.pointer and target.pointer.context_text else ""),
        ]
        if part
    )
    score = 0.0
    matched_keywords: list[str] = []
    for keyword in POSITIVE_KEYWORDS:
        if keyword in combined:
            score += 0.22
            matched_keywords.append(keyword)
    for keyword in NEGATIVE_KEYWORDS:
        if keyword in combined:
            score -= 0.3
    if target.content_type:
        lowered = target.content_type.lower()
        if "pdf" in lowered:
            score += 0.15
        if "html" in lowered:
            score += 0.05
    if target.is_accessible:
        score += 0.05
    target.classification_confidence = max(0.0, min(score, 1.0))
    target.matched_keywords = matched_keywords
    if target.classification_confidence >= 0.4:
        target.target_type = "annual_report" if "pdf" in target.content_type.lower() else "annual_report_page"
    return target
