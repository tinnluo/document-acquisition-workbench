from __future__ import annotations

from doc_workbench.providers.sec import SecRegulatoryFilingsProvider


class RegulatoryFilingsProvider(SecRegulatoryFilingsProvider):
    """Neutral public regulatory-filings adapter backed by an SEC implementation."""
