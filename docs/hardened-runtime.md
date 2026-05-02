# Hardened Local Runtime

This document describes the demo-level hardening story for running
`doc-workbench` in a container. It is not a full production isolation
platform. The goal is to show that the repository's safety model extends
from policy files to runtime configuration.

## What This Covers

- Non-root container execution
- Read-only code area
- Writable workspace / output separation
- Host-mounted output where needed
- Langfuse suppression in isolated runs

## Quick Start

```bash
# Build the image
docker build -t doc-workbench:local .

# Create the host workspace directory (required for the bind mount)
mkdir -p workspace

# Run any CLI command in the hardened container
docker run --rm \
  --user 65534:65534 \
  --read-only \
  --tmpfs /tmp \
  -v "$PWD/workspace:/workspace" \
  -v "$PWD/examples:/app/examples:ro" \
  -e DOC_WORKBENCH_HOME=/workspace \
  doc-workbench:local \
  discover --entities examples/public_companies.csv
```

Or with docker compose (see `docker-compose.yml` for the full definition):

```bash
mkdir -p workspace
docker compose run --rm workbench discover --entities examples/public_companies.csv
docker compose run --rm workbench eval
```

## Hardening Details

### Non-root user

The Dockerfile switches to `USER nobody` before the entrypoint. The
`docker run` and `docker-compose.yml` examples reinforce this by passing
`--user 65534:65534` explicitly.

No UID 0 privileges are available at runtime, so a process that escapes
the application sandbox cannot write to system paths.

### Read-only code area

The `--read-only` flag mounts the container's root filesystem as
read-only. Only explicitly declared writable surfaces can be written:

- `/workspace` — the bind-mounted host directory for run outputs
- `/tmp` — a `tmpfs` mount for Python's own ephemeral writes

The `/app` code area cannot be modified at runtime.

### Writable workspace / output separation

The runtime never writes to `/app`. All outputs — run directories,
registry files, trace JSON files, eval reports — go to `/workspace`,
which maps to `./workspace/` on the host.

This separation means:

- Outputs survive the container lifecycle.
- The code image remains clean and cacheable.
- A reviewer can inspect all run artifacts directly from the host.

### Host-mounted examples (optional)

The `examples/` directory can be bind-mounted read-only so the default
entity CSV is available without baking it into the image:

```
-v "$PWD/examples:/app/examples:ro"
```

This is optional. The image also contains the package's bundled policy
files and eval fixtures, so `doc-workbench eval` works without any mount.

### Langfuse suppression

In isolated or CI environments, leave `DOC_WORKBENCH_ENABLE_LANGFUSE`
unset. The runtime falls back to local-only tracing automatically. No
API keys are required for offline runs.

The `doc-workbench eval` command suppresses Langfuse regardless of
environment variable state.

## What This Is Not

- Not a full gVisor / seccomp / AppArmor configuration.
- Not a network egress filter.
- Not a kernel-level sandbox.

This is a demo-hardening story. It shows that the default deployment
posture is least-privilege and write-segregated, not that it constitutes
production-grade isolation.

## Verification

After building and running the container:

```bash
# Artifacts appear on the host
ls workspace/runs/
ls workspace/traces/

# Eval passes locally without remote credentials
docker compose run --rm workbench eval
```

Both paths should complete without requiring any environment variables.
