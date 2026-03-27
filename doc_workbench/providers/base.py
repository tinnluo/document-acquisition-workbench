from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from doc_workbench.models import EntityRecord


@dataclass(slots=True)
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""
    domain: str = ""


class SearchProvider(Protocol):
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...


class DiscoveryProvider(Protocol):
    async def discover(self, entity: EntityRecord) -> list[dict]: ...
