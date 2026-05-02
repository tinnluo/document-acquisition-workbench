"""Focused unit tests for doc_workbench.http_utils safe_get / safe_head.

Uses respx to mock httpx at the transport level, exercising:
- per-hop domain enforcement on redirects
- Content-Length preflight blocking oversized responses
- streaming chunk-abort on chunked oversized responses
- HEAD success path
- HEAD 4xx fallback to Range-GET
- Range-GET reads at most one chunk even when server ignores Range
- safe_head redirect per-hop domain enforcement
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from doc_workbench.execution_policy import PolicyViolationError
from doc_workbench.http_utils import safe_get, safe_head


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(allowed_families: list[str] | None = None, max_bytes: int = 10_000_000) -> Any:
    """Return a minimal ExecutionPolicy with explicit MIME and source allowlists."""
    from doc_workbench.execution_policy import (
        DownloadPolicy, ExecutionPolicy, FollowupSearchPolicy, RegistryPolicy,
    )
    return ExecutionPolicy(
        allowed_command_stages=[],
        allowed_source_families=allowed_families if allowed_families is not None else ["*"],
        download=DownloadPolicy(
            enabled=True,
            max_count=50,
            max_file_size_bytes=max_bytes,
            allowed_mime_types=["application/pdf", "text/html"],
        ),
        followup_search=FollowupSearchPolicy(enabled=True),
        registry=RegistryPolicy(root_restriction="registry"),
        policy_path="<test>",
    )


# ---------------------------------------------------------------------------
# safe_get tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_safe_get_follows_redirect_and_returns_body() -> None:
    body = b"<html>annual report</html>"
    respx.get("https://example.com/start").mock(
        return_value=httpx.Response(301, headers={"location": "https://example.com/final"})
    )
    respx.get("https://example.com/final").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, content=body)
    )

    content, ct, final_url = await safe_get("https://example.com/start")
    assert content == body
    assert "text/html" in ct
    assert "final" in final_url


@pytest.mark.asyncio
@respx.mock
async def test_safe_get_blocks_redirect_to_disallowed_domain() -> None:
    """Redirect from allowed → blocked domain must raise PolicyViolationError."""
    respx.get("https://example.com/start").mock(
        return_value=httpx.Response(301, headers={"location": "https://evil.com/payload"})
    )
    policy = _make_policy(["example.com"])

    with pytest.raises(PolicyViolationError, match="evil.com"):
        await safe_get("https://example.com/start", exec_policy=policy)


@pytest.mark.asyncio
@respx.mock
async def test_safe_get_content_length_preflight_blocks_oversized() -> None:
    """Content-Length header exceeding max_file_size_bytes must be blocked before body read."""
    large_body = b"x" * 100
    respx.get("https://example.com/big.html").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": str(len(large_body))},
            content=large_body,
        )
    )
    policy = _make_policy(max_bytes=50)

    with pytest.raises(PolicyViolationError):
        await safe_get("https://example.com/big.html", exec_policy=policy)


@pytest.mark.asyncio
@respx.mock
async def test_safe_get_streaming_chunk_abort_blocks_oversized() -> None:
    """Streaming response without Content-Length must be aborted once accumulated
    bytes exceed max_file_size_bytes."""
    large_body = b"A" * 200
    respx.get("https://example.com/chunked").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=large_body,
        )
    )
    policy = _make_policy(max_bytes=50)

    with pytest.raises(PolicyViolationError):
        await safe_get("https://example.com/chunked", exec_policy=policy)


# ---------------------------------------------------------------------------
# safe_head tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_safe_head_success() -> None:
    respx.head("https://example.com/report.pdf").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/pdf"})
    )

    ct, status, final_url = await safe_head("https://example.com/report.pdf")
    assert status == 200
    assert ct == "application/pdf"
    assert "example.com" in final_url


@pytest.mark.asyncio
@respx.mock
async def test_safe_head_4xx_falls_back_to_range_get() -> None:
    """HEAD returning 405 should fall back to a Range-GET; result status is 200."""
    respx.head("https://example.com/report.pdf").mock(
        return_value=httpx.Response(405)
    )
    # Range-GET returns 206
    respx.get("https://example.com/report.pdf").mock(
        return_value=httpx.Response(206, headers={"content-type": "application/pdf"}, content=b"x")
    )

    ct, status, _ = await safe_head("https://example.com/report.pdf")
    assert status == 200  # 206 normalised to success
    assert ct == "application/pdf"


@pytest.mark.asyncio
@respx.mock
async def test_safe_head_redirect_blocks_disallowed_hop() -> None:
    """HEAD redirect to a blocked domain must raise PolicyViolationError."""
    respx.head("https://allowed.com/start").mock(
        return_value=httpx.Response(301, headers={"location": "https://blocked.com/doc.pdf"})
    )
    policy = _make_policy(["allowed.com"])

    with pytest.raises(PolicyViolationError, match="blocked.com"):
        await safe_head("https://allowed.com/start", exec_policy=policy)
