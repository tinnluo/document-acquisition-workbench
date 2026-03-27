# Document Acquisition Workbench

`doc_workbench` is a CLI-driven demo for discovering public annual reports or filings, reviewing candidates, downloading approved documents, and recording metadata in a local registry.

This public demo rebuilds a production architectural pattern in a public-safe form. It preserves system design, module boundaries, and execution flow while removing proprietary business logic and internal data.

- What this demo shows: a CLI-driven acquisition workbench for discovery, follow-up resolution, review, download, and metadata scanning.
- Which production capability it mirrors: a modular document-acquisition pipeline with provider abstraction, registry-backed tracking, and staged candidate processing.
- What was intentionally generalized or removed: proprietary business rules, internal datasets, internal rollout logic, and company-specific storage conventions.
- Why this repo exists in the portfolio: to show how a production workflow can be reconstructed as a runnable public demo without exposing private implementation context.

## Install

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Commands

```bash
doc-workbench paths
doc-workbench discover --entities examples/public_companies.csv --followup-search
doc-workbench followup-search --input workspace/runs/discover_*/discover.json
doc-workbench review --input workspace/runs/discover_*/discover.json
doc-workbench download --input workspace/runs/review_*/review_queue.csv
doc-workbench scan --all
```

## Workflow

- `discover` crawls official company pages, adds a regulatory fallback for entities with a CIK, and optionally enriches results with API-backed web search.
- `discover --followup-search` runs the second-stage follow-up pipeline in preview mode and appends promoted targets to the discovery artifact.
- `followup-search` materializes web-search surfaced seed URLs and promoted targets into the registry before review.
- `review` converts discovery results into a queue with `approved`, `needs_review`, and `rejected` recommendations.
- `download` fetches approved PDFs into `workspace/registry`.
- `scan` extracts lightweight PDF metadata and updates each registry manifest.

## Context Policy

The repo now carries an explicit, readable acquisition policy under `context/`. It is rule-based rather than prompt-heavy and is loaded into both discovery and review so the same source-priority and recommendation guardrails show up in every run artifact.

```yaml
acquisition_order:
  - official_site
  - regulatory_filings
  - search_expansion
  - followup_extraction
```

Each `discover` and `review` run writes a `resolved_policy.json` sidecar so the exact policy used for the run is preserved with the outputs.

## Observability

The repo uses a lightweight local tracer instead of a generic agent runtime. It emits JSON traces under `workspace/traces/` with stage-level spans for discovery, follow-up, ranking, and review queue generation.

Provider selection trace excerpt:

```json
{
  "entity_id": "msft",
  "stage": "official_site_lookup",
  "provider": "official_site",
  "candidate_count_out": 3,
  "top_candidate_url": "https://www.microsoft.com/investor/reports/annual-report-2024.pdf"
}
```

## Decision Trace

Discovery and review both emit explainability sidecars:

- `ranking_trace.json` explains candidate scoring inputs and final confidence
- `review_trace.json` explains confidence band, reason codes, and recommendation

Candidate scoring excerpt:

```json
{
  "url": "https://example.com/annual-report-2024.pdf",
  "source_tier": "official",
  "same_domain_match": true,
  "pdf_bonus": 0.15,
  "keyword_match": true,
  "final_confidence": 0.95
}
```

Review trace excerpt:

```json
{
  "candidate_url": "https://example.com/annual-report-2024.pdf",
  "confidence_band": "approved_band",
  "reason_codes": ["source_tier:official", "confidence_band:approved_band", "same_domain", "pdf"],
  "final_recommendation": "approved"
}
```

## Public Design Choices

- Local-first runtime paths under `workspace/`
- Official-source prioritization with fallback acquisition
- Neutral `regulatory_filings` abstraction with a public SEC-backed implementation
- Registry-backed downloads with dedupe
- Small public example dataset in [`examples/public_companies.csv`](examples/public_companies.csv)

See [`docs/architecture.md`](docs/architecture.md) for the subsystem overview.
