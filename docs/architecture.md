# Architecture

`doc_workbench` is a public demo of a document acquisition workflow:

1. Discover candidates from official company sites.
2. Add a neutral regulatory-filings fallback for public issuers with a known identifier.
3. Run a second-stage follow-up pipeline over web-search surfaced URLs to extract and resolve direct document targets.
4. Export a review queue that can be inspected or edited.
5. Download approved documents into a local registry.
6. Scan downloaded PDFs and write metadata back to the registry manifest.

The repo keeps runtime state under `workspace/` by default and treats search APIs as optional.

The public provider surface is intentionally generic:

- `official_site` for on-domain discovery
- `search` for optional search API expansion
- `regulatory_filings` as the public fallback abstraction

The current regulatory implementation is SEC-backed for the sample dataset, but the discovery flow imports the neutral adapter rather than the regulator-specific module.

The follow-up stage mirrors the source repo's `annual-reports` pattern in a lighter public form:

- fetch search seed pages or PDFs
- extract follow-up pointers
- resolve and score candidate targets
- optionally materialize seeds and targets into the registry for provenance
- pass promoted targets forward to the existing review/download flow
