from __future__ import annotations

from doc_workbench.http_utils import safe_get


async def download_bytes(
    url: str,
    exec_policy=None,
) -> tuple[bytes, str, str]:
    """Fetch *url* and return ``(content_bytes, content_type, final_url)``.

    ``content_type`` is the primary MIME type extracted from the
    ``Content-Type`` response header (parameters stripped).

    ``final_url`` is the URL after following any redirects.

    Redirects are followed **manually** via :func:`~doc_workbench.http_utils.safe_get`
    so that *exec_policy* domain enforcement fires at every hop before any
    data is transferred.  Callers should still enforce size/MIME on the
    returned content after this call returns.
    """
    return await safe_get(url, exec_policy=exec_policy, timeout=45.0)
