from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader


def scan_pdf(path: Path) -> dict[str, Any]:
    reader = PdfReader(BytesIO(path.read_bytes()))
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
