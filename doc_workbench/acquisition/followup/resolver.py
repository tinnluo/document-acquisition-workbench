from __future__ import annotations

from urllib.parse import urljoin

from doc_workbench.acquisition.followup.models import FollowupPointer, ResolvedTarget
from doc_workbench.http_utils import safe_head


async def resolve_pointer(
    pointer: FollowupPointer,
    exec_policy=None,
) -> ResolvedTarget:
    """Resolve *pointer* with per-hop domain enforcement.

    Uses :func:`~doc_workbench.http_utils.safe_head` which follows redirects
    manually and validates each ``Location`` against *exec_policy* before
    sending the next request.  This prevents an allowlisted URL from
    silently 30x-redirecting to a blocked host.

    :class:`~doc_workbench.execution_policy.PolicyViolationError` is intentionally
    **re-raised** so callers and traces can distinguish a policy block from an
    ordinary network failure.  Only non-policy network/IO exceptions are caught
    and converted to an inaccessible-target sentinel.
    """
    from doc_workbench.execution_policy import PolicyViolationError

    resolved_url = (
        pointer.url
        if pointer.url.startswith(("http://", "https://"))
        else urljoin(pointer.source_url, pointer.url)
    )
    try:
        content_type, status_code, final_url = await safe_head(
            resolved_url, exec_policy=exec_policy, timeout=20.0
        )
        return ResolvedTarget(
            original_url=pointer.url,
            resolved_url=resolved_url,
            final_url=final_url,
            content_type=content_type,
            status_code=status_code,
            is_accessible=status_code < 400,
            pointer=pointer,
        )
    except PolicyViolationError:
        # Policy blocks are deterministic and must propagate so the caller
        # can record them as policy errors rather than generic link failures.
        raise
    except Exception:
        return ResolvedTarget(
            original_url=pointer.url,
            resolved_url=resolved_url,
            final_url=resolved_url,
            status_code=0,
            is_accessible=False,
            pointer=pointer,
        )
