from __future__ import annotations

import httpx


async def download_bytes(url: str) -> bytes:
    headers = {"User-Agent": "doc-workbench/0.1 (public demo)"}
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
