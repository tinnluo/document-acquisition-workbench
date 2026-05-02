# HANDOVER

Repo: `document-acquisition-workbench`

## Objective

Build on the shipped acquisition workbench and make this repo the portfolio's clearest demo of:

- permissioned document acquisition
- execution-policy sidecars
- safe-by-default download workflow
- hardened local runtime

This repo should show action permissioning and acquisition safety, not a cloud sandbox product.

## Current shipped baseline

The following are already shipped and must be preserved:

- explicit context policy with `resolved_policy.json` sidecars
- legacy CLI orchestration and shipped LangGraph execution path
- stable artifact contracts for discovery, ranking, review, and downloads
- packaged eval harness with machine-readable report output
- local JSON traces
- optional Langfuse bridge with explicit opt-in

This handover extends the existing acquisition and traceability story rather than replacing it.

## Required shipped outcome

A reviewer should be able to see that document acquisition is governed by both:

- **context policy** for acquisition strategy
- **execution policy** for what the runtime is allowed to do

Minimum shipped outcome:

1. Add a bundled `execution_policy.yaml` separate from the current context policy.
2. Emit `resolved_execution_policy.json` for each run.
3. Enforce policy for command stages, domains, downloads, file size, and file types.
4. Ensure both legacy and LangGraph execution paths respect the same execution policy.
5. Add hardened local runtime guidance and runnable container path.

## In scope

- bundled execution-policy file
- policy resolution and sidecar artifact emission
- policy enforcement for at least:
  - allowed command stages
  - allowed source/domain families
  - max download count
  - max file size
  - allowed MIME/content types
  - registry root restrictions
  - whether follow-up extraction is permitted
- explicit permissioning for `download` and `followup-search`
- docs for hardened local runtime
- tests and eval compatibility

## Out of scope

- cloud sandbox infrastructure
- browser isolation services
- enterprise DLP integrations
- adding unrelated new acquisition sources just to exercise policy
- changing current output filenames

## Required public framing

Use wording like:

- permissioned document acquisition
- execution-policy sidecars
- safe-by-default download workflow
- hardened local runtime
- traceable acquisition decisions

Do not describe it as:

- a cloud sandbox platform
- a malware scanning product
- a generic autonomous web agent

## Required interfaces, artifacts, and config surfaces

Add or expose:

- bundled file: `execution_policy.yaml`
- per-run artifact: `resolved_execution_policy.json`

Policy must govern at least:

- command-stage permissions
- domain/source family permissions
- `download`
- `followup-search`
- file-size limits
- allowed MIME/content types
- registry write location rules

Preserve:

- `discover.json`
- `ranking_trace.json`
- `review_queue.csv`
- `review_trace.json`
- existing local trace contract
- `evals/latest_report.json`

## Implementation guidance

### 1. Keep context policy and execution policy separate

The repo already answers:

- where to search
- in what order

The new layer should answer:

- what the runtime is allowed to do
- what it may write
- what it may download

### 2. Resolve policy per run

Like the current context-policy sidecar, each run should emit the resolved execution policy so reviewers can reconstruct the exact constraints in force.

### 3. Enforce at the same boundaries in both engines

The legacy path and LangGraph path must respect the same policy model.

Do not let one engine bypass permissions that the other enforces.

### 4. Fail deterministically before unsafe writes

Examples:

- blocked domain should fail before fetch/download
- disallowed MIME or oversize file should fail before registry write
- forbidden follow-up extraction should stop before stage execution

### 5. Document hardened local runtime

Add a real, runnable local-hardening story:

- non-root container execution
- read-only code area
- writable workspace/output separation
- host-mounted output only where needed

This is a demo-hardening story, not a full production isolation platform.

## Likely files to modify

- `README.md`
- `docs/architecture.md`
- `context/...`
- `doc_workbench/config.py`
- `doc_workbench/cli.py`
- `doc_workbench/...`
- `Dockerfile`
- `docker-compose.yml` if needed
- `tests/...`
- `evals/...`

## Verification commands

Run the repo's real paths after implementation. At minimum:

```bash
pytest tests/
doc-workbench eval
doc-workbench discover --help
doc-workbench review --help
```

Add implementation-specific verification for:

- blocked domain rejection
- oversize or disallowed file rejection before registry write
- blocked `followup-search`
- legacy path policy enforcement
- LangGraph path policy enforcement
- eval runs remaining local with remote tracing suppressed

## Guardrails

- Do not break current artifact names or output locations.
- Do not collapse context policy and execution policy into one file.
- Do not make Langfuse mandatory.
- Do not claim hardened isolation features that the repo does not actually implement.
- Do not let one orchestration engine bypass execution policy checks.

## Acceptance standard

Accept the implementation only if all of the following are true:

1. `execution_policy.yaml` is shipped and documented.
2. Every run emits `resolved_execution_policy.json`.
3. `download` and `followup-search` are explicitly permissionable.
4. Blocked domains and blocked file types are rejected deterministically.
5. Legacy and LangGraph paths enforce the same execution policy.
6. Existing artifact contracts remain unchanged.
7. Eval runs still pass and remain local-safe.
8. README and architecture docs describe the shipped safety model accurately.
