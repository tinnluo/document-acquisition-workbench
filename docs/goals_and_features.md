# Goals and Features — Document Acquisition Workbench

## Goal

Demonstrate a production-grade, policy-driven document acquisition pipeline that discovers, reviews, downloads, and tracks public filings (annual reports, SEC/regulatory filings) across large company universes.

The repo shows how to build a modular, traceable acquisition system where every decision — which source to prefer, which candidates to approve, which documents to download — is rule-governed, explainable, and reproducible rather than opaque or prompt-driven.

## What This Solves

Building reliable document pipelines at scale requires more than scraping. Sources go stale, regulatory providers need fallbacks, candidates need scoring, and reviewers need explainability. This repo shows the architectural layer that sits between raw retrieval and downstream use: structured source prioritization, ranked candidate scoring, staged review, and registry-backed tracking.

---

## Features

### Multi-Stage CLI Workflow

Five composable pipeline stages, each producing an artifact the next stage consumes:

| Stage | Command | Output |
|---|---|---|
| Discovery | `discover` | `discover.json` with ranked candidates |
| Follow-up search | `followup-search` | Promotes web-search targets into registry |
| Review | `review` | `review_queue.csv` with recommendations |
| Download | `download` | PDFs fetched into `workspace/registry/` |
| Scan | `scan` | Metadata manifests per document |

### Source-Priority Context Policy

An explicit, readable acquisition policy in `context/` governs source preference order:

```yaml
acquisition_order:
  - official_site
  - regulatory_filings
  - search_expansion
  - followup_extraction
```

The same policy is loaded for both discovery and review, and a `resolved_policy.json` sidecar is written with every run so the exact rules applied are preserved alongside outputs.

### Provider Abstraction

Regulatory filing providers (e.g., SEC/EDGAR) are behind a neutral `regulatory_filings` abstraction. The public implementation uses EDGAR. The design isolates provider-specific logic so alternative registries can be substituted without touching pipeline logic.

### Candidate Scoring and Explainability

Every discovered candidate receives a confidence score based on:
- Source tier (official, regulatory, search-expanded)
- Domain match against known company domains
- PDF bonus
- Keyword match in URL

Scoring is written to `ranking_trace.json` alongside each run. Review outputs include `review_trace.json` with confidence band, reason codes, and final recommendation.

### Registry-Backed Deduplication

All downloads are tracked in a local registry. Re-running the pipeline against the same entities skips already-acquired documents without re-downloading.

### Local Observability

A lightweight local tracer (no external service required) emits JSON trace artifacts under `workspace/traces/`:
- Stage-level spans with latency, provider, input/output counts
- Decision fields where routing choices were made

### Docker Support

Full Docker and Docker Compose setup. Bind-mount keeps `workspace/` outputs on the host for easy inspection.

---

## What This Repo Does Not Cover

- Downstream parsing or structured extraction of document content (see `evidence-enrichment-engine`)
- Full-text indexing or vector search over retrieved documents
- Entity resolution or entity master management (see `entity-data-lakehouse`)
