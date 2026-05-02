from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader


def scan_pdf(path: Path, content_type: str = "application/pdf") -> dict[str, Any]:
    """Scan *path* and return extracted metadata.

    If *content_type* indicates a non-PDF artifact the scan is skipped and a
    ``skipped`` status is returned so callers don't crash on HTML or binary
    files that were legitimately stored under ``annual_reports``.
    """
    normalized_ct = (content_type or "").split(";")[0].strip().lower()
    if "pdf" not in normalized_ct and not str(path).lower().endswith(".pdf"):
        return {
            "title": "",
            "issuer_name": "",
            "reporting_period": "",
            "publication_date": "",
            "page_count": None,
            "modality": "non_pdf",
            "status": "skipped",
            "error": f"scan skipped: content_type={content_type!r} is not PDF",
        }
    try:
        reader = PdfReader(BytesIO(path.read_bytes()))
    except Exception as exc:
        return {
            "title": "",
            "issuer_name": "",
            "reporting_period": "",
            "publication_date": "",
            "page_count": None,
            "modality": "unknown",
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    metadata = reader.metadata or {}
    first_page_text = ""
    if reader.pages:
        try:
            first_page_text = reader.pages[0].extract_text() or ""
        except Exception:
            first_page_text = ""
    title = str(metadata.get("/Title") or "").strip()
    issuer = ""
    if first_page_text:
        issuer = first_page_text.splitlines()[0].strip()[:120]
    page_count = len(reader.pages)
    modality = "text_selectable" if any((page.extract_text() or "").strip() for page in reader.pages[:2]) else "image_or_unknown"
    publication_date = str(metadata.get("/CreationDate") or "").strip()
    return {
        "title": title,
        "issuer_name": issuer,
        "reporting_period": "",
        "publication_date": publication_date,
        "page_count": page_count,
        "modality": modality,
        "status": "complete",
        "error": "",
    }
