"""Execution policy — governs what the doc-workbench runtime is allowed to do.

This is separate from :mod:`doc_workbench.policy` (context policy), which
governs *acquisition strategy* (where to search, in what order).

The execution policy answers:
- which command stages may run
- which domains may be fetched/downloaded
- what file sizes and MIME types are accepted before registry write
- whether follow-up extraction is permitted
- where the registry may write files

Resolution order when *policy_path* is ``None``:
1. ``importlib.resources`` — works after ``pip install``, including zip wheels.
2. Repo-relative ``context/execution_policy.yaml`` — fallback for un-installed
   source-tree runs.
"""
from __future__ import annotations

import hashlib
import importlib.resources
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PolicyViolationError(RuntimeError):
    """Raised when a runtime action is blocked by the execution policy.

    Always raised *before* the unsafe action (fetch, write, stage execution)
    so the system fails deterministically without side-effects.
    """


# ---------------------------------------------------------------------------
# Policy dataclasses
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DownloadPolicy:
    enabled: bool
    max_count: int
    max_file_size_bytes: int
    allowed_mime_types: list[str]


@dataclass(slots=True)
class FollowupSearchPolicy:
    enabled: bool


@dataclass(slots=True)
class RegistryPolicy:
    root_restriction: str


@dataclass(slots=True)
class ExecutionPolicy:
    allowed_command_stages: list[str]
    allowed_source_families: list[str]
    download: DownloadPolicy
    followup_search: FollowupSearchPolicy
    registry: RegistryPolicy
    policy_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load_execution_policy_yaml(policy_path: str | Path | None) -> tuple[dict[str, Any], str]:
    """Load raw YAML payload and return (payload, resolved_path_str)."""
    if policy_path is not None:
        p = Path(policy_path)
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}, str(p)

    # Package-data path (zip-safe).
    try:
        ref = importlib.resources.files("doc_workbench.context").joinpath("execution_policy.yaml")
        with importlib.resources.as_file(ref) as p:
            content = Path(p).read_text(encoding="utf-8")
            resolved = str(p)
        return yaml.safe_load(content) or {}, resolved
    except (ModuleNotFoundError, FileNotFoundError, TypeError):
        pass

    # Fallback: resolve relative to the repo root (two levels up from this
    # file: doc_workbench/execution_policy.py → doc_workbench/ → repo root).
    # Using __file__ avoids CWD-relative resolution, which would silently load
    # the wrong policy if the process is started from an unexpected directory.
    _repo_root = Path(__file__).resolve().parents[1]
    fallback = _repo_root / "context" / "execution_policy.yaml"
    if fallback.exists():
        return yaml.safe_load(fallback.read_text(encoding="utf-8")) or {}, str(fallback)

    raise FileNotFoundError(
        "Cannot locate execution_policy.yaml. "
        "Install the package with 'pip install -e .' or run from the repo root."
    )


def _parse_bool(value: object, field_name: str) -> bool:
    """Strictly parse a boolean policy field.

    Rejects non-boolean values (e.g. the string ``"false"``) rather than
    silently coercing them with ``bool()``.  A YAML string ``"false"`` would
    become truthy under ``bool()``, which is fail-open for security controls
    like ``download.enabled`` and ``followup_search.enabled``.

    Raises :class:`ValueError` with a clear message for any non-bool value so
    the operator can fix the policy file rather than running in an unintended
    state.
    """
    if isinstance(value, bool):
        return value
    raise ValueError(
        f"execution_policy: '{field_name}' must be a boolean (true/false), "
        f"got {type(value).__name__} {value!r}. "
        "Quoted values like \"false\" are not valid — use unquoted YAML booleans."
    )


def load_execution_policy(policy_path: str | Path | None = None) -> ExecutionPolicy:
    """Parse and validate execution_policy.yaml into an :class:`ExecutionPolicy`."""
    payload, resolved_path_str = _load_execution_policy_yaml(policy_path)

    dl_raw = payload.get("download") or {}
    fu_raw = payload.get("followup_search") or {}
    reg_raw = payload.get("registry") or {}

    max_count = int(dl_raw.get("max_count", 50))
    max_bytes = int(dl_raw.get("max_file_size_bytes", 52_428_800))
    if max_count < 0:
        raise ValueError("execution_policy: download.max_count must be >= 0")
    if max_bytes < 0:
        raise ValueError("execution_policy: download.max_file_size_bytes must be >= 0")

    # allowed_mime_types: fail closed when the key is absent.
    # An explicit empty list [] blocks all MIME types.
    # A wildcard ["*"] must be intentional — never inferred.
    raw_mime = dl_raw.get("allowed_mime_types")
    if raw_mime is None:
        # Key absent in a custom policy file → block all rather than allow all.
        allowed_mime_types: list[str] = []
    else:
        allowed_mime_types = list(raw_mime)

    # registry.root_restriction: empty string means no restriction (any path
    # under workspace root is allowed).  This is intentional for operators who
    # do not want to constrain write paths; it is not a security regression
    # since the workspace root itself is validated by WorkspacePaths.
    return ExecutionPolicy(
        allowed_command_stages=list(payload.get("allowed_command_stages") or []),
        # If the YAML omits allowed_source_families entirely we default to an
        # empty list (block all) rather than ["*"] (allow all) so that a
        # misconfigured or minimal policy file fails closed.
        allowed_source_families=list(payload.get("allowed_source_families") or []),
        download=DownloadPolicy(
            enabled=_parse_bool(dl_raw.get("enabled", True), "download.enabled"),
            max_count=max_count,
            max_file_size_bytes=max_bytes,
            allowed_mime_types=allowed_mime_types,
        ),
        followup_search=FollowupSearchPolicy(
            enabled=_parse_bool(fu_raw.get("enabled", True), "followup_search.enabled"),
        ),
        registry=RegistryPolicy(
            root_restriction=str(reg_raw.get("root_restriction") or ""),
        ),
        policy_path=resolved_path_str,
    )


def write_resolved_execution_policy(path: Path, policy: ExecutionPolicy) -> Path:
    """Serialise the resolved execution policy to *path* as JSON."""
    path.write_text(json.dumps(policy.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Enforcement helpers
# ---------------------------------------------------------------------------

def enforce_command_stage(policy: ExecutionPolicy, stage: str) -> None:
    """Raise :class:`PolicyViolationError` if *stage* is not in the allowlist.

    Fail-closed: an empty ``allowed_command_stages`` list blocks **all** stages.
    This prevents a partial or malformed custom policy file from silently
    disabling stage gating.  The bundled default policy explicitly lists every
    permitted stage, so an empty list only arises from a misconfigured file.
    """
    allowed = policy.allowed_command_stages
    if stage not in allowed:
        raise PolicyViolationError(
            f"Command stage '{stage}' is not permitted by execution policy. "
            f"Allowed stages: {allowed}"
        )


def enforce_domain(policy: ExecutionPolicy, url: str) -> None:
    """Raise :class:`PolicyViolationError` if the URL's domain is blocked.

    A domain is allowed when:
    - ``allowed_source_families`` contains ``"*"``, OR
    - the URL's hostname ends with one of the listed suffixes.

    Raises *before* any network call.
    """
    families = policy.allowed_source_families
    if "*" in families:
        return
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        hostname = ""
    for suffix in families:
        if hostname == suffix or hostname.endswith(f".{suffix}"):
            return
    raise PolicyViolationError(
        f"Domain '{hostname}' is not in the allowed source families: {families}. "
        f"Blocked before fetch for URL: {url}"
    )


def enforce_download_enabled(policy: ExecutionPolicy) -> None:
    """Raise :class:`PolicyViolationError` if downloads are disabled."""
    if not policy.download.enabled:
        raise PolicyViolationError(
            "Downloads are disabled by execution policy (download.enabled: false)."
        )


def enforce_download_count(policy: ExecutionPolicy, current_count: int) -> None:
    """Raise :class:`PolicyViolationError` if *current_count* has reached the cap."""
    if current_count >= policy.download.max_count:
        raise PolicyViolationError(
            f"Download count limit reached ({policy.download.max_count}). "
            "Blocked before registry write."
        )


def enforce_file_size(policy: ExecutionPolicy, byte_size: int, url: str = "") -> None:
    """Raise :class:`PolicyViolationError` if *byte_size* exceeds the policy limit."""
    limit = policy.download.max_file_size_bytes
    if byte_size > limit:
        raise PolicyViolationError(
            f"File size {byte_size:,} bytes exceeds execution policy limit "
            f"{limit:,} bytes. Blocked before registry write."
            + (f" URL: {url}" if url else "")
        )


def enforce_mime_type(policy: ExecutionPolicy, content_type: str, url: str = "") -> None:
    """Raise :class:`PolicyViolationError` if *content_type* is not allowed.

    Matching is done on the primary type/subtype, ignoring parameters
    (e.g. ``charset``).  A ``"*"`` entry in the allowlist permits all types.
    """
    allowed = policy.download.allowed_mime_types
    if "*" in allowed:
        return
    # Normalise: strip parameters, lowercase.
    primary = content_type.split(";")[0].strip().lower()
    for entry in allowed:
        if entry.strip().lower() == primary:
            return
    raise PolicyViolationError(
        f"MIME type '{primary}' is not in the allowed list: {allowed}. "
        "Blocked before registry write."
        + (f" URL: {url}" if url else "")
    )


def enforce_followup_search(policy: ExecutionPolicy) -> None:
    """Raise :class:`PolicyViolationError` if follow-up search is disabled."""
    if not policy.followup_search.enabled:
        raise PolicyViolationError(
            "Follow-up search is disabled by execution policy "
            "(followup_search.enabled: false)."
        )


def enforce_registry_root(policy: ExecutionPolicy, write_path: Path, workspace_root: Path) -> None:
    """Raise :class:`PolicyViolationError` if *write_path* is outside the allowed registry root.

    When ``registry.root_restriction`` is empty, any path under *workspace_root* is allowed.
    """
    restriction = policy.registry.root_restriction
    if not restriction:
        # No restriction — any path under workspace_root is fine.
        allowed_root = workspace_root
    else:
        allowed_root = workspace_root / restriction
    try:
        write_path.resolve().relative_to(allowed_root.resolve())
    except ValueError:
        raise PolicyViolationError(
            f"Registry write path '{write_path}' is outside the allowed root "
            f"'{allowed_root}'. Blocked before write."
        )
