FROM python:3.11-slim

# ---------------------------------------------------------------------------
# Hardened local runtime
#
# Security model (demo-level hardening):
#   - Non-root user: code runs as 'nobody' — no UID 0 privileges.
#   - Read-only code area: /app is never written by the running process.
#     All outputs go to /workspace, which is a separate writable mount.
#   - Minimal image: slim base, no shell tools beyond what pip needs.
#
# Volume guidance:
#   Mount a host directory to /workspace to capture run outputs.
#   Do NOT mount the host source tree to /app in production runs.
#   See docker-compose.yml for a complete hardened run example.
# ---------------------------------------------------------------------------

WORKDIR /app

# Install dependencies before copying source so this layer is cached on
# dependency-only changes.
COPY pyproject.toml README.md ./
COPY doc_workbench/ ./doc_workbench/
RUN pip install --no-cache-dir .

# Create the workspace directory owned by nobody before switching user.
# The host-mounted volume will overlay this, but it needs to exist for
# runs where no volume is mounted (e.g. CI smoke tests).
RUN mkdir -p /workspace && chown nobody:nogroup /workspace

# Switch to non-root user.  All subsequent operations — including doc-workbench
# CLI runs — execute as nobody.
USER nobody

# Default workspace root for containerised runs.
ENV DOC_WORKBENCH_HOME=/workspace

ENTRYPOINT ["doc-workbench"]
