# Document Acquisition Workbench

`doc_workbench` is a CLI-driven demo for discovering public annual reports or filings, reviewing candidates, downloading approved documents, and recording metadata in a local registry.

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

## Public Design Choices

- Local-first runtime paths under `workspace/`
- Official-source prioritization with fallback acquisition
- Neutral `regulatory_filings` abstraction with a public SEC-backed implementation
- Registry-backed downloads with dedupe
- Small public example dataset in [`examples/public_companies.csv`](/Users/lxt/Documents/portfolio/document-acquisition-workbench/examples/public_companies.csv)

See [`docs/architecture.md`](/Users/lxt/Documents/portfolio/document-acquisition-workbench/docs/architecture.md) for the subsystem overview.
