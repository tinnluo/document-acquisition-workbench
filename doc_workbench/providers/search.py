from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from doc_workbench.providers.base import SearchProvider, SearchResult

_SERPER_ENDPOINT = "https://google.serper.dev/search"
_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class NullSearchProvider:
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        del query, max_results
        return []


class SerperSearchProvider:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        payload = {"q": query, "num": max(1, min(int(max_results), 10))}
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.post(_SERPER_ENDPOINT, headers=headers, json=payload)
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

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        headers = {"Accept": "application/json", "X-Subscription-Token": self.api_key}
        params = {"q": query, "count": max(1, min(int(max_results), 10)), "result_filter": "web"}
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(_BRAVE_ENDPOINT, headers=headers, params=params)
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
