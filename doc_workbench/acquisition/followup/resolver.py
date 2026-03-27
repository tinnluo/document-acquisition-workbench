from __future__ import annotations

from urllib.parse import urljoin

import httpx

from doc_workbench.acquisition.followup.models import FollowupPointer, ResolvedTarget


async def resolve_pointer(pointer: FollowupPointer) -> ResolvedTarget:
    resolved_url = pointer.url if pointer.url.startswith(("http://", "https://")) else urljoin(pointer.source_url, pointer.url)
    headers = {"User-Agent": "doc-workbench/0.1 (public demo)"}
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
            try:
                response = await client.head(resolved_url)
            except Exception:
                response = await client.get(resolved_url)
            if int(response.status_code) >= 400:
                response = await client.get(resolved_url)
        return ResolvedTarget(
            original_url=pointer.url,
            resolved_url=resolved_url,
            final_url=str(response.url),
            content_type=str(response.headers.get("content-type") or "").split(";")[0].strip(),
            status_code=int(response.status_code),
            is_accessible=int(response.status_code) < 400,
            pointer=pointer,
        )
    except Exception:
        return ResolvedTarget(
            original_url=pointer.url,
            resolved_url=resolved_url,
            final_url=resolved_url,
            status_code=0,
            is_accessible=False,
            pointer=pointer,
        )
