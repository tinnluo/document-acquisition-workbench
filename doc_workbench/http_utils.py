"""Shared HTTP utilities for doc-workbench.

Key invariants
--------------
1. **Manual redirect following** — ``follow_redirects=False`` everywhere.
   ``enforce_domain`` is called on every ``Location`` hop before the next
   request is issued, preventing an allowlisted URL from 30x-redirecting to
   a blocked host before the policy check fires.

2. **Size enforcement before buffering** — ``safe_get`` checks the
   ``Content-Length`` response header against ``exec_policy.max_file_size_bytes``
   (when available) before reading any body bytes, and enforces the limit again
   while streaming so the cap is respected even for chunked/unknown-length
   responses.

3. **Body-free HEAD probing** — ``safe_head`` uses a ``Range: bytes=0-0``
   GET probe as a fallback when HEAD is unsupported or returns >=400, so the
   resolver path never buffers a full response body during link classification.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx

if TYPE_CHECKING:
    from doc_workbench.execution_policy import ExecutionPolicy

_USER_AGENT = "doc-workbench/0.1 (public demo)"
_MAX_REDIRECTS = 10
# Chunk size for streaming reads in safe_get.
_CHUNK_SIZE = 65_536  # 64 KiB


def _next_url(location: str, current_url: str) -> str:
    return location if location.startswith(("http://", "https://")) else urljoin(current_url, location)


def _enforce_domain(exec_policy: "ExecutionPolicy | None", url: str) -> None:
    if exec_policy is not None:
        from doc_workbench.execution_policy import enforce_domain
        enforce_domain(exec_policy, url)


def _preflight_size(exec_policy: "ExecutionPolicy | None", headers: httpx.Headers, url: str) -> None:
    """Raise PolicyViolationError if Content-Length header exceeds policy limit."""
    if exec_policy is None:
        return
    raw = headers.get("content-length", "")
    if not raw:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    from doc_workbench.execution_policy import enforce_file_size
    enforce_file_size(exec_policy, declared, url)


async def safe_get(
    url: str,
    *,
    exec_policy: "ExecutionPolicy | None" = None,
    timeout: float = 45.0,
) -> tuple[bytes, str, str]:
    """Fetch *url* with per-hop domain enforcement and streaming size cap.

    Returns ``(content_bytes, content_type, final_url)``.

    - ``enforce_domain`` fires on the initial URL and every redirect hop.
    - ``Content-Length`` is checked against ``max_file_size_bytes`` before
      any body bytes are read (when the server declares it).
    - The size limit is also enforced while streaming, so chunked/unknown-
      length responses are caught before buffering the full body.
    """
    _enforce_domain(exec_policy, url)

    headers = {"User-Agent": _USER_AGENT}
    current_url = url

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers=headers,
    ) as client:
        for _ in range(_MAX_REDIRECTS):
            async with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    next_url = _next_url(location, current_url)
                    _enforce_domain(exec_policy, next_url)
                    current_url = next_url
                    continue

                response.raise_for_status()

                # Preflight: check declared Content-Length before reading.
                _preflight_size(exec_policy, response.headers, str(response.url))

                raw_ct = response.headers.get("content-type", "application/octet-stream")
                content_type = raw_ct.split(";")[0].strip().lower() or "application/octet-stream"
                final_url = str(response.url)

                # Stream body, enforcing size cap per chunk.
                chunks: list[bytes] = []
                total = 0
                limit = exec_policy.download.max_file_size_bytes if exec_policy is not None else None
                async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                    total += len(chunk)
                    if limit is not None and total > limit:
                        from doc_workbench.execution_policy import PolicyViolationError
                        raise PolicyViolationError(
                            f"Response body exceeded max_file_size_bytes limit "
                            f"{limit:,} bytes while streaming {final_url}"
                        )
                    chunks.append(chunk)

                return b"".join(chunks), content_type, final_url

    raise httpx.TooManyRedirects(f"Exceeded {_MAX_REDIRECTS} redirects for {url}")


async def safe_head(
    url: str,
    *,
    exec_policy: "ExecutionPolicy | None" = None,
    timeout: float = 20.0,
) -> tuple[str, int, str]:
    """Probe *url* metadata with per-hop domain enforcement.

    Returns ``(content_type, status_code, final_url)``.

    Strategy:
    - Try HEAD first (no body, cheap).
    - If HEAD fails (network error) or returns >=400, fall back to a
      ``Range: bytes=0-0`` GET, which retrieves at most 1 byte.  This avoids
      buffering the full response body during link classification.
    - Both HEAD and Range-GET responses may themselves be redirects; all hops
      are validated via ``enforce_domain`` before following.
    """
    _enforce_domain(exec_policy, url)

    headers = {"User-Agent": _USER_AGENT}
    current_url = url

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers=headers,
    ) as client:
        for _ in range(_MAX_REDIRECTS):
            # --- HEAD attempt ---
            head_ok = False
            try:
                response = await client.head(current_url)
                head_ok = True
            except Exception:
                pass

            if head_ok:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    next_url = _next_url(location, current_url)
                    _enforce_domain(exec_policy, next_url)
                    current_url = next_url
                    continue
                if int(response.status_code) < 400:
                    ct = str(response.headers.get("content-type") or "").split(";")[0].strip()
                    return ct, int(response.status_code), str(response.url)
                # HEAD returned >=400 — fall through to Range-GET probe below.

            # --- Range-GET probe (body-free fallback) ---
            # Use Range: bytes=0-0 to ask for a 1-byte slice.  Some servers
            # ignore Range and return 200 with the full body; guard against that
            # by reading at most one _CHUNK_SIZE chunk and then closing the
            # stream.  We only need headers — the body is discarded.
            range_headers = {**headers, "Range": "bytes=0-0"}
            async with client.stream("GET", current_url, headers=range_headers) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    next_url = _next_url(location, current_url)
                    _enforce_domain(exec_policy, next_url)
                    current_url = next_url
                    # Discard at most one chunk before following the redirect.
                    async for _ in response.aiter_bytes(_CHUNK_SIZE):
                        break
                    continue
                # Discard at most one chunk — we only care about headers.
                async for _ in response.aiter_bytes(_CHUNK_SIZE):
                    break
                ct = str(response.headers.get("content-type") or "").split(";")[0].strip()
                # Treat 206 Partial Content or 200 as accessible; 4xx/5xx as not.
                status = int(response.status_code)
                accessible_status = 200 if status in (200, 206) else status
                return ct, accessible_status, str(response.url)

    raise httpx.TooManyRedirects(f"Exceeded {_MAX_REDIRECTS} redirects for {url}")
