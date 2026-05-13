"""Tests for the Langfuse observability bridge.

Verifies that:
- A no-op stub is returned when the opt-in flag is absent
- A no-op stub is returned when credentials are absent (even with flag set)
- The no-op stub's flush_span method does not raise
- The cached client is reused across calls
- reset_langfuse_client() clears the cache
- trace_id propagation and cache invalidation work correctly
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


def test_trace_id_cache_invalidation(monkeypatch) -> None:
    """When trace_id changes, get_langfuse_client should return a new client instance."""
    monkeypatch.delenv("DOC_WORKBENCH_ENABLE_LANGFUSE", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    client_a = get_langfuse_client(trace_id="trace-123")
    client_b = get_langfuse_client(trace_id="trace-123")
    assert client_a is client_b, "Same trace_id should return cached client"

    client_c = get_langfuse_client(trace_id="trace-456")
    assert client_c is not client_a, "Different trace_id should return new client"


def test_trace_id_none_to_value_invalidates_cache(monkeypatch) -> None:
    """Transitioning from trace_id=None to a specific trace_id should invalidate cache."""
    monkeypatch.delenv("DOC_WORKBENCH_ENABLE_LANGFUSE", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    client_a = get_langfuse_client(trace_id=None)
    client_b = get_langfuse_client(trace_id="trace-789")
    assert client_b is not client_a, "Changing from None to trace_id should create new client"


def test_trace_id_value_to_none_invalidates_cache(monkeypatch) -> None:
    """Transitioning from a specific trace_id to None should invalidate cache."""
    monkeypatch.delenv("DOC_WORKBENCH_ENABLE_LANGFUSE", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    client_a = get_langfuse_client(trace_id="trace-abc")
    client_b = get_langfuse_client(trace_id=None)
    assert client_b is not client_a, "Changing from trace_id to None should create new client"


def test_real_langfuse_client_uses_correct_api(monkeypatch) -> None:
    """Verify that the real Langfuse client uses the 4.x SDK API correctly."""
    monkeypatch.setenv("DOC_WORKBENCH_ENABLE_LANGFUSE", "1")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")

    import sys
    import types

    # Track API calls
    calls = []

    class MockObservation:
        def __init__(self, name, as_type, trace_context=None, input=None, output=None):
            calls.append(("observation_init", name, as_type, trace_context, input, output))
            self.name = name

        def start_observation(self, name, as_type, input=None, output=None):
            calls.append(("child_observation", name, as_type, input, output))
            return MockObservation(name, as_type, input=input, output=output)

        def end(self):
            calls.append(("observation_end", self.name))

    class MockLangfuse:
        def __init__(self, **kwargs):
            calls.append(("langfuse_init", kwargs))

        def start_observation(self, trace_context=None, name=None, as_type=None):
            calls.append(("root_observation", trace_context, name, as_type))
            return MockObservation(name, as_type, trace_context)

        def flush(self):
            calls.append(("flush",))

    fake_module = types.ModuleType("langfuse")
    fake_module.Langfuse = MockLangfuse
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    trace_id = "abc123def456"
    client = get_langfuse_client(trace_id=trace_id)

    # Verify root observation was created with correct trace_id
    assert any(
        call[0] == "root_observation" and
        call[1] == {"trace_id": trace_id} and
        call[2] == "doc-workbench-run" and
        call[3] == "span"
        for call in calls
    ), f"Root observation not created correctly. Calls: {calls}"

    # Verify flush_span creates child observation
    calls.clear()
    client.flush_span(
        stage="test_stage",
        entity_id="TEST_001",
        latency_ms=10.5,
        candidate_count_out=3,
    )

    # Should create child observation and flush
    assert any(call[0] == "child_observation" and call[1] == "test_stage" for call in calls), \
        f"Child observation not created. Calls: {calls}"
    assert any(call[0] == "flush" for call in calls), \
        f"Flush not called. Calls: {calls}"

