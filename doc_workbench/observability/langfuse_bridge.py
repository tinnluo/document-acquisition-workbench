"""Optional Langfuse remote observability bridge.

Behaviour
---------
- Remote tracing requires **both** credentials AND an explicit opt-in flag:
    - ``LANGFUSE_SECRET_KEY`` and ``LANGFUSE_PUBLIC_KEY`` must be set, AND
    - ``DOC_WORKBENCH_ENABLE_LANGFUSE=1`` must be set.
  This two-gate design prevents accidental data egress in CI or shared shells
  where Langfuse credentials may be present in the environment globally.
- If either gate is absent, or if the ``langfuse`` package raises any
  exception during initialisation, a silent no-op stub is returned instead.
- The local ``RunTrace`` / JSON traces under ``workspace/traces/`` are
  **not** affected by this module; they operate independently.

Data egress notice
------------------
When enabled, entity IDs and candidate URLs are sent to the configured
Langfuse host after each pipeline node.  Do not enable remote tracing if
you are processing data that must not leave the local machine.

Usage
-----
    from doc_workbench.observability.langfuse_bridge import get_langfuse_client

    lf = get_langfuse_client()
    if lf is not None:
        lf.flush_span(stage="discover", entity_id="AAPL", ...)

Enabling remote tracing
-----------------------
    export DOC_WORKBENCH_ENABLE_LANGFUSE=1
    export LANGFUSE_SECRET_KEY=sk-...
    export LANGFUSE_PUBLIC_KEY=pk-...
    export LANGFUSE_HOST=https://cloud.langfuse.com  # optional
"""

from __future__ import annotations

import atexit
import os
from typing import Any


class _NoOpLangfuseClient:
    """Stub returned when remote tracing is disabled or init fails."""

    def flush_span(self, **kwargs: Any) -> None:  # noqa: ANN401
        pass  # no-op: remote tracing disabled

    def shutdown(self) -> None:
        pass


class _LangfuseClient:
    """Thin wrapper around the real Langfuse SDK client.

    Flushes after every span so that short-lived CLI runs do not silently
    drop remote traces.  An atexit hook also calls shutdown() as a safety net.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._trace = client.trace(name="doc-workbench-run")
        atexit.register(self.shutdown)

    def flush_span(
        self,
        *,
        stage: str,
        entity_id: str,
        latency_ms: float = 0.0,
        candidate_count_in: int = 0,
        candidate_count_out: int = 0,
        top_candidate_url: str = "",
        top_confidence: float = 0.0,
        **extra: Any,
    ) -> None:
        try:
            self._trace.span(
                name=stage,
                input={"entity_id": entity_id},
                output={
                    "latency_ms": latency_ms,
                    "candidate_count_in": candidate_count_in,
                    "candidate_count_out": candidate_count_out,
                    "top_candidate_url": top_candidate_url,
                    "top_confidence": top_confidence,
                    **extra,
                },
            )
            # Flush immediately so short-lived CLI runs don't lose spans.
            self._client.flush()
        except Exception:
            # Never crash the acquisition workflow due to observability failures.
            pass

    def shutdown(self) -> None:
        try:
            self._client.flush()
        except Exception:
            pass


_cached_client: _LangfuseClient | _NoOpLangfuseClient | None = None
_initialised: bool = False


def get_langfuse_client() -> _LangfuseClient | _NoOpLangfuseClient:
    """Return a Langfuse client (real or no-op), initialised at most once per process.

    Returns a no-op client unless ALL of these conditions are met:
    - ``DOC_WORKBENCH_ENABLE_LANGFUSE=1`` is set (explicit opt-in)
    - ``LANGFUSE_SECRET_KEY`` is set
    - ``LANGFUSE_PUBLIC_KEY`` is set
    - The ``langfuse`` package is installed and initialises without error
    """
    global _cached_client, _initialised  # noqa: PLW0603
    if _initialised:
        return _cached_client  # type: ignore[return-value]

    _initialised = True

    # Explicit opt-in gate — must be set to "1" to enable remote tracing.
    enable_flag = os.environ.get("DOC_WORKBENCH_ENABLE_LANGFUSE", "").strip()
    if enable_flag != "1":
        _cached_client = _NoOpLangfuseClient()
        return _cached_client

    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()

    if not secret_key or not public_key:
        _cached_client = _NoOpLangfuseClient()
        return _cached_client

    try:
        from langfuse import Langfuse  # type: ignore[import-untyped]

        host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com").strip()
        raw_client = Langfuse(
            secret_key=secret_key,
            public_key=public_key,
            host=host,
        )
        _cached_client = _LangfuseClient(raw_client)
    except Exception:
        _cached_client = _NoOpLangfuseClient()

    return _cached_client


def reset_langfuse_client() -> None:
    """Reset cached client — used in tests to inject a fresh state."""
    global _cached_client, _initialised  # noqa: PLW0603
    _cached_client = None
    _initialised = False
