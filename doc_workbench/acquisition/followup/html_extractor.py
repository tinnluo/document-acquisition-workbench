from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from doc_workbench.acquisition.followup.models import FollowupPointer

TEXT_URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def extract_html_pointers(html_content: str, source_url: str) -> list[FollowupPointer]:
    soup = BeautifulSoup(html_content or "", "html.parser")
    seen: set[str] = set()
    pointers: list[FollowupPointer] = []

    for anchor in soup.find_all("a", href=True):
        href = urljoin(source_url, str(anchor.get("href") or "").strip())
        if not href.startswith(("http://", "https://")) or href in seen:
            continue
        seen.add(href)
        pointers.append(
            FollowupPointer(
                url=href,
                pointer_type="href",
                source_url=source_url,
                anchor_text=anchor.get_text(" ", strip=True),
                extraction_method="html_href",
            )
        )

    body_text = soup.get_text(" ", strip=True)
    for match in TEXT_URL_PATTERN.finditer(body_text):
        url = match.group(0).rstrip(".,;:)>]\"'")
        if url in seen:
            continue
        seen.add(url)
        pointers.append(
            FollowupPointer(
                url=url,
                pointer_type="text_url",
                source_url=source_url,
                context_text=body_text[max(0, match.start() - 80) : match.end() + 80].strip(),
                extraction_method="html_text_regex",
            )
        )

    return pointers
