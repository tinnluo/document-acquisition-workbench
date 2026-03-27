from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from doc_workbench.models import EntityRecord


def _normalize_cik(cik: str) -> str:
    digits = "".join(char for char in cik if char.isdigit())
    return digits.zfill(10) if digits else ""


@dataclass(slots=True)
class SecRegulatoryFilingsProvider:
    user_agent: str = "doc-workbench/0.1 (public demo)"

    async def discover(self, entity: EntityRecord) -> list[dict]:
        normalized_cik = _normalize_cik(entity.cik)
        if not normalized_cik:
            return []
        url = f"https://data.sec.gov/submissions/CIK{normalized_cik}.json"
        headers = {"User-Agent": self.user_agent}
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        recent = ((payload or {}).get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        primary_documents = recent.get("primaryDocument") or []
        for index, form in enumerate(forms):
            if str(form or "").upper() not in {"10-K", "20-F", "40-F"}:
                continue
            accession = str(accessions[index] or "")
            primary_document = str(primary_documents[index] or "")
            filing_date = str(dates[index] or "")
            accession_compact = accession.replace("-", "")
            page_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(normalized_cik)}/{accession_compact}/{primary_document}"
            )
            year = None
            if filing_date[:4].isdigit():
                year = int(filing_date[:4]) - 1
            current_year = datetime.now(UTC).year
            confidence = 0.88 if year and year >= current_year - 2 else 0.8
            return [
                {
                    "url": page_url,
                    "title": f"{entity.name} {form} filing",
                    "snippet": filing_date,
                    "source_type": "regulatory_filings",
                    "source_tier": "regulatory",
                    "document_kind": "regulatory_filing",
                    "year": year,
                    "confidence": confidence,
                    "reasons": [f"sec_form:{form}", f"filing_date:{filing_date}"],
                }
            ]
        return []


SecFilingsProvider = SecRegulatoryFilingsProvider
