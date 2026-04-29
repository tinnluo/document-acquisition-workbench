"""Tests for the Langfuse observability bridge.

Verifies that:
- A no-op stub is returned when the opt-in flag is absent
- A no-op stub is returned when credentials are absent (even with flag set)
- The no-op stub's flush_span method does not raise
- The cached client is reused across calls
- reset_langfuse_client() clears the cache
"""

from __future__ import annotations

import pytest

from doc_workbench.observability.langfuse_bridge import (
    _NoOpLangfuseClient,
    get_langfuse_client,
    reset_langfuse_client,
)


@pytest.fixture(autouse=True)
def _reset_between_tests():
    """Ensure each test starts with a fresh client cache."""
    reset_langfuse_client()
    yield
    reset_langfuse_client()


def test_no_op_returned_without_opt_in_flag(monkeypatch) -> None:
    """When DOC_WORKBENCH_ENABLE_LANGFUSE is absent, get_langfuse_client returns a no-op."""
    monkeypatch.delenv("DOC_WORKBENCH_ENABLE_LANGFUSE", raising=False)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")

    client = get_langfuse_client()
    assert isinstance(client, _NoOpLangfuseClient)


def test_no_op_returned_without_credentials(monkeypatch) -> None:
    """When credentials are absent (even with flag set), get_langfuse_client returns a no-op."""
    monkeypatch.setenv("DOC_WORKBENCH_ENABLE_LANGFUSE", "1")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    client = get_langfuse_client()
    assert isinstance(client, _NoOpLangfuseClient)


def test_no_op_flush_span_does_not_raise(monkeypatch) -> None:
    """No-op stub's flush_span must never raise."""
    monkeypatch.delenv("DOC_WORKBENCH_ENABLE_LANGFUSE", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    client = get_langfuse_client()
    # Should not raise regardless of arguments
    client.flush_span(
        stage="discover",
        entity_id="TEST",
        candidate_count_out=3,
        top_candidate_url="https://example.com/ar.pdf",
        top_confidence=0.85,
    )


def test_cached_client_reused(monkeypatch) -> None:
    """get_langfuse_client should return the exact same object on repeated calls."""
    monkeypatch.delenv("DOC_WORKBENCH_ENABLE_LANGFUSE", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    client_a = get_langfuse_client()
    client_b = get_langfuse_client()
    assert client_a is client_b


def test_reset_clears_cache(monkeypatch) -> None:
    """reset_langfuse_client should allow a fresh client to be created."""
    monkeypatch.delenv("DOC_WORKBENCH_ENABLE_LANGFUSE", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    get_langfuse_client()
    reset_langfuse_client()
    client_b = get_langfuse_client()
    assert isinstance(client_b, _NoOpLangfuseClient)


def test_no_op_returned_when_only_one_credential_set(monkeypatch) -> None:
    """Partial credentials should still produce a no-op (both keys are required)."""
    monkeypatch.setenv("DOC_WORKBENCH_ENABLE_LANGFUSE", "1")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    client = get_langfuse_client()
    assert isinstance(client, _NoOpLangfuseClient)


def test_no_op_returned_on_langfuse_init_failure(monkeypatch) -> None:
    """If Langfuse SDK raises during __init__, a no-op stub should be returned gracefully."""
    monkeypatch.setenv("DOC_WORKBENCH_ENABLE_LANGFUSE", "1")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")

    import sys
    import types

    fake_module = types.ModuleType("langfuse")

    class _BrokenLangfuse:
        def __init__(self, **kwargs):
            raise RuntimeError("Simulated Langfuse init failure")

    fake_module.Langfuse = _BrokenLangfuse
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    client = get_langfuse_client()
    assert isinstance(client, _NoOpLangfuseClient)

