from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

from doc_workbench.http_utils import safe_get
from doc_workbench.providers.base import SearchProvider, SearchResult

_SERPER_ENDPOINT = "https://google.serper.dev/search"
_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# Fixed API endpoint hosts — enforce domain before each request.
_SERPER_HOST = "google.serper.dev"
_BRAVE_HOST = "api.search.brave.com"


class NullSearchProvider:
    async def search(self, query: str, max_results: int = 5, exec_policy: Any = None) -> list[SearchResult]:
        del query, max_results, exec_policy
        return []


class SerperSearchProvider:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 5, exec_policy: Any = None) -> list[SearchResult]:
        # Enforce domain on the fixed API endpoint before sending credentials.
        if exec_policy is not None:
            from doc_workbench.execution_policy import enforce_domain
            enforce_domain(exec_policy, _SERPER_ENDPOINT)

        import httpx
        payload = {"q": query, "num": max(1, min(int(max_results), 10))}
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        # Use follow_redirects=False — Serper API does not redirect; if it ever
        # does, the redirect would go to an unexpected host.
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, headers=headers) as client:
            response = await client.post(_SERPER_ENDPOINT, json=payload)
            response.raise_for_status()
            data = response.json()
        results: list[SearchResult] = []
        for item in data.get("organic", []) if isinstance(data, dict) else []:
            url = str(item.get("link") or "")
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=str(item.get("title") or ""),
                    snippet=str(item.get("snippet") or ""),
                    domain=urlparse(url).netloc,
                )
            )
        return results


class BraveSearchProvider:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 5, exec_policy: Any = None) -> list[SearchResult]:
        # Enforce domain on the fixed API endpoint before sending credentials.
        if exec_policy is not None:
            from doc_workbench.execution_policy import enforce_domain
            enforce_domain(exec_policy, _BRAVE_ENDPOINT)

        import httpx
        headers = {"Accept": "application/json", "X-Subscription-Token": self.api_key}
        params = {"q": query, "count": max(1, min(int(max_results), 10)), "result_filter": "web"}
        # Use follow_redirects=False — Brave Search API does not redirect.
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, headers=headers) as client:
            response = await client.get(_BRAVE_ENDPOINT, params=params)
            response.raise_for_status()
            data = response.json()
        web = data.get("web", {}) if isinstance(data, dict) else {}
        results: list[SearchResult] = []
        for item in web.get("results", []) if isinstance(web, dict) else []:
            url = str(item.get("url") or "")
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=str(item.get("title") or ""),
                    snippet=str(item.get("description") or item.get("snippet") or ""),
                    domain=urlparse(url).netloc,
                )
            )
        return results


def get_search_provider() -> SearchProvider:
    if os.environ.get("SERPER_API_KEY"):
        return SerperSearchProvider(os.environ["SERPER_API_KEY"])
    if os.environ.get("BRAVE_API_KEY"):
        return BraveSearchProvider(os.environ["BRAVE_API_KEY"])
    return NullSearchProvider()
