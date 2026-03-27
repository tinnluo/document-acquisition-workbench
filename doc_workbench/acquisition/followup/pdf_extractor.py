from __future__ import annotations

import re

from doc_workbench.acquisition.followup.models import FollowupPointer

URL_PATTERN = re.compile(rb"https?://[^\s<>\")\]']+", re.IGNORECASE)


def extract_pdf_pointers(pdf_bytes: bytes, source_url: str) -> list[FollowupPointer]:
    pointers: list[FollowupPointer] = []
    seen: set[str] = set()
    for match in URL_PATTERN.finditer(pdf_bytes):
        try:
            url = match.group(0).decode("utf-8", errors="ignore").rstrip(".,;:)>]\"'")
        except Exception:
            continue
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        pointers.append(
            FollowupPointer(
                url=url,
                pointer_type="text_url",
                source_url=source_url,
                extraction_method="pdf_bytes_regex",
            )
        )
    return pointers
