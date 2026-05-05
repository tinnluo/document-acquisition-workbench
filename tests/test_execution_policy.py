"""Tests for execution policy loading, enforcement, and CLI sidecar emission.

Coverage:
- load_execution_policy() defaults
- write_resolved_execution_policy() roundtrip
- enforce_command_stage: allowed and blocked
- enforce_domain: wildcard, allowed suffix, blocked domain
- enforce_download_enabled: disabled flag
- enforce_download_count: at and below cap
- enforce_file_size: under and over limit
- enforce_mime_type: allowed, wildcard, blocked
- enforce_followup_search: enabled and disabled
- enforce_registry_root: inside and outside restriction
- CLI: resolved_execution_policy.json emitted by discover
- CLI: blocked command stage exits non-zero
- CLI: blocked followup-search stage exits non-zero
- LangGraph followup_node: respects exec_policy.followup_search.enabled
- Eval runs still pass (local-safe)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from doc_workbench import cli
from doc_workbench.execution_policy import (
    DownloadPolicy,
    ExecutionPolicy,
    FollowupSearchPolicy,
    PolicyViolationError,
    RegistryPolicy,
    enforce_command_stage,
    enforce_domain,
    enforce_download_count,
    enforce_download_enabled,
    enforce_file_size,
    enforce_followup_search,
    enforce_mime_type,
    enforce_registry_root,
    load_execution_policy,
    write_resolved_execution_policy,
)
from doc_workbench.models import DiscoveryCandidate, DiscoveryRecord

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(
    *,
    allowed_command_stages: list[str] | None = None,
    allowed_source_families: list[str] | None = None,
    download_enabled: bool = True,
    max_count: int = 50,
    max_file_size_bytes: int = 52_428_800,
    allowed_mime_types: list[str] | None = None,
    followup_enabled: bool = True,
    root_restriction: str = "registry",
) -> ExecutionPolicy:
    return ExecutionPolicy(
        allowed_command_stages=allowed_command_stages or ["discover", "review", "download", "followup-search"],
        allowed_source_families=allowed_source_families or ["*"],
        download=DownloadPolicy(
            enabled=download_enabled,
            max_count=max_count,
            max_file_size_bytes=max_file_size_bytes,
            allowed_mime_types=allowed_mime_types or ["*"],
        ),
        followup_search=FollowupSearchPolicy(enabled=followup_enabled),
        registry=RegistryPolicy(root_restriction=root_restriction),
        policy_path="<test>",
    )


# ---------------------------------------------------------------------------
# Stage 1: load and write
# ---------------------------------------------------------------------------

def test_load_execution_policy_defaults() -> None:
    policy = load_execution_policy()
    assert "discover" in policy.allowed_command_stages
    assert "download" in policy.allowed_command_stages
    assert "followup-search" in policy.allowed_command_stages
    assert "scan" in policy.allowed_command_stages
    assert policy.download.enabled is True
    assert policy.download.max_count > 0
    assert policy.download.max_file_size_bytes > 0
    assert policy.followup_search.enabled is True
    assert policy.registry.root_restriction == "registry"
    # The bundled default must NOT contain a bare wildcard — that would disable
    # MIME enforcement entirely and conflict with the safe-by-default guarantee.
    assert "*" not in policy.download.allowed_mime_types, (
        "Default execution_policy.yaml must not contain '*' in allowed_mime_types; "
        "use an explicit allowlist so MIME enforcement is active out of the box."
    )
    # application/octet-stream is a generic catch-all for unknown binaries; it
    # must not be in the default allowlist so that unknown content is rejected
    # unless the operator explicitly opts in.
    assert "application/octet-stream" not in policy.download.allowed_mime_types, (
        "Default execution_policy.yaml must not allow 'application/octet-stream'; "
        "unknown binary content should be blocked by default."
    )
    # The default policy must not use the wildcard domain allowlist — that
    # disables egress control entirely and is only appropriate for demo/test.
    assert "*" not in policy.allowed_source_families, (
        "Default execution_policy.yaml must not contain '*' in allowed_source_families; "
        "use an explicit domain allowlist so egress control is active out of the box."
    )


def test_execution_policy_yaml_bundled_and_readable() -> None:
    """The bundled execution policy YAML is the single canonical source.

    The root-level context/ directory was removed — doc_workbench/context/ is
    the only copy.  This test verifies the bundled file is present, readable,
    and parses as valid YAML with the expected top-level keys.
    """
    import importlib.resources
    repo_root = Path(__file__).resolve().parents[1]

    try:
        ref = importlib.resources.files("doc_workbench.context").joinpath("execution_policy.yaml")
        with importlib.resources.as_file(ref) as p:
            content = Path(p).read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError, TypeError):
        content = (repo_root / "doc_workbench" / "context" / "execution_policy.yaml").read_text(encoding="utf-8")

    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict), "execution_policy.yaml must parse as a YAML mapping"
    for key in ("allowed_command_stages", "allowed_source_families", "download", "followup_search"):
        assert key in parsed, f"expected top-level key '{key}' missing from execution_policy.yaml"


def test_load_execution_policy_from_custom_file(tmp_path: Path) -> None:
    custom = tmp_path / "ep.yaml"
    custom.write_text(
        yaml.dump({
            "allowed_command_stages": ["discover"],
            "allowed_source_families": ["sec.gov"],
            "download": {"enabled": False, "max_count": 5, "max_file_size_bytes": 1024, "allowed_mime_types": ["application/pdf"]},
            "followup_search": {"enabled": False},
            "registry": {"root_restriction": "out"},
        }),
        encoding="utf-8",
    )
    policy = load_execution_policy(custom)
    assert policy.allowed_command_stages == ["discover"]
    assert policy.allowed_source_families == ["sec.gov"]
    assert policy.download.enabled is False
    assert policy.download.max_count == 5
    assert policy.followup_search.enabled is False
    assert policy.registry.root_restriction == "out"


def test_write_resolved_execution_policy_roundtrip(tmp_path: Path) -> None:
    policy = load_execution_policy()
    out = tmp_path / "resolved_execution_policy.json"
    result = write_resolved_execution_policy(out, policy)
    assert result == out
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["download"]["max_count"] == policy.download.max_count
    assert "allowed_command_stages" in data
    assert data["digest"] if "digest" in data else True  # optional field


def test_execution_policy_has_digest() -> None:
    policy = load_execution_policy()
    d = policy.digest
    assert len(d) == 16
    # Digest is deterministic.
    assert policy.digest == d


# ---------------------------------------------------------------------------
# Stage 3: enforce_command_stage
# ---------------------------------------------------------------------------

def test_enforce_command_stage_allowed() -> None:
    policy = _make_policy(allowed_command_stages=["discover", "review"])
    enforce_command_stage(policy, "discover")  # no exception


def test_enforce_command_stage_blocked() -> None:
    policy = _make_policy(allowed_command_stages=["discover"])
    with pytest.raises(PolicyViolationError, match="download"):
        enforce_command_stage(policy, "download")


def test_enforce_command_stage_empty_list_blocks_all() -> None:
    # Empty allowlist = fail closed: every stage is blocked.
    # This prevents a partial/malformed custom policy from silently disabling
    # stage gating.
    policy = _make_policy(allowed_command_stages=["discover"])
    empty_policy = ExecutionPolicy(
        allowed_command_stages=[],
        allowed_source_families=["*"],
        download=policy.download,
        followup_search=policy.followup_search,
        registry=policy.registry,
        policy_path="<test>",
    )
    with pytest.raises(PolicyViolationError, match="anything"):
        enforce_command_stage(empty_policy, "anything")


# ---------------------------------------------------------------------------
# Stage 3: enforce_domain
# ---------------------------------------------------------------------------

def test_enforce_domain_wildcard_allows_any() -> None:
    policy = _make_policy(allowed_source_families=["*"])
    enforce_domain(policy, "https://evil.example.com/report.pdf")  # no exception


def test_enforce_domain_allowed_suffix() -> None:
    policy = _make_policy(allowed_source_families=["sec.gov", "example.com"])
    enforce_domain(policy, "https://www.sec.gov/report.pdf")  # no exception
    enforce_domain(policy, "https://example.com/doc.pdf")      # no exception


def test_enforce_domain_blocked_raises_before_fetch() -> None:
    policy = _make_policy(allowed_source_families=["sec.gov"])
    with pytest.raises(PolicyViolationError, match="evil.com"):
        enforce_domain(policy, "https://evil.com/malware.pdf")


def test_enforce_domain_exact_match() -> None:
    policy = _make_policy(allowed_source_families=["example.com"])
    # Subdomain should also be allowed (ends with .example.com).
    enforce_domain(policy, "https://docs.example.com/report.pdf")


# ---------------------------------------------------------------------------
# Stage 3: enforce_download_enabled
# ---------------------------------------------------------------------------

def test_enforce_download_enabled_passes() -> None:
    policy = _make_policy(download_enabled=True)
    enforce_download_enabled(policy)  # no exception


def test_enforce_download_disabled_raises() -> None:
    policy = _make_policy(download_enabled=False)
    with pytest.raises(PolicyViolationError, match="disabled"):
        enforce_download_enabled(policy)


# ---------------------------------------------------------------------------
# Stage 3: enforce_download_count
# ---------------------------------------------------------------------------

def test_enforce_download_count_below_cap() -> None:
    policy = _make_policy(max_count=10)
    enforce_download_count(policy, 9)  # no exception


def test_enforce_download_count_at_cap_raises() -> None:
    policy = _make_policy(max_count=10)
    with pytest.raises(PolicyViolationError, match="10"):
        enforce_download_count(policy, 10)


# ---------------------------------------------------------------------------
# Stage 3: enforce_file_size
# ---------------------------------------------------------------------------

def test_enforce_file_size_under_limit() -> None:
    policy = _make_policy(max_file_size_bytes=1000)
    enforce_file_size(policy, 999)  # no exception


def test_enforce_file_size_over_limit_raises_before_write() -> None:
    policy = _make_policy(max_file_size_bytes=1000)
    with pytest.raises(PolicyViolationError, match="1,001"):
        enforce_file_size(policy, 1001, url="https://example.com/huge.pdf")


# ---------------------------------------------------------------------------
# Stage 3: enforce_mime_type
# ---------------------------------------------------------------------------

def test_enforce_mime_type_wildcard_allows_any() -> None:
    policy = _make_policy(allowed_mime_types=["*"])
    enforce_mime_type(policy, "application/x-shockwave-flash")  # no exception


def test_enforce_mime_type_allowed() -> None:
    policy = _make_policy(allowed_mime_types=["application/pdf", "text/html"])
    enforce_mime_type(policy, "application/pdf")
    enforce_mime_type(policy, "text/html; charset=utf-8")  # params stripped


def test_enforce_mime_type_blocked_raises_before_write() -> None:
    policy = _make_policy(allowed_mime_types=["application/pdf"])
    with pytest.raises(PolicyViolationError, match="application/zip"):
        enforce_mime_type(policy, "application/zip", url="https://example.com/archive.zip")


# ---------------------------------------------------------------------------
# Stage 3: enforce_followup_search
# ---------------------------------------------------------------------------

def test_enforce_followup_search_enabled() -> None:
    policy = _make_policy(followup_enabled=True)
    enforce_followup_search(policy)  # no exception


def test_enforce_followup_search_disabled_raises() -> None:
    policy = _make_policy(followup_enabled=False)
    with pytest.raises(PolicyViolationError, match="disabled"):
        enforce_followup_search(policy)


# ---------------------------------------------------------------------------
# Stage 3: enforce_registry_root
# ---------------------------------------------------------------------------

def test_enforce_registry_root_inside_restriction(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    policy = _make_policy(root_restriction="registry")
    write_path = workspace / "registry" / "annual_reports" / "doc.pdf"
    enforce_registry_root(policy, write_path, workspace)  # no exception


def test_enforce_registry_root_outside_raises(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    policy = _make_policy(root_restriction="registry")
    write_path = tmp_path / "escape" / "doc.pdf"  # outside workspace entirely
    with pytest.raises(PolicyViolationError, match="outside"):
        enforce_registry_root(policy, write_path, workspace)


def test_enforce_registry_root_empty_restriction_allows_any(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    policy = _make_policy(root_restriction="")
    write_path = workspace / "anywhere" / "doc.pdf"
    enforce_registry_root(policy, write_path, workspace)  # no exception


# ---------------------------------------------------------------------------
# Stage 2: CLI sidecar emission
# ---------------------------------------------------------------------------

def test_discover_emits_resolved_execution_policy(tmp_path: Path, monkeypatch) -> None:
    """discover command writes resolved_execution_policy.json alongside resolved_policy.json."""
    workspace = tmp_path / "workspace"
    entities_csv = tmp_path / "entities.csv"
    entities_csv.write_text(
        "entity_id,name,ticker,official_website,cik,country\n"
        "1001,Example Corp,EXM,https://example.com,,US\n",
        encoding="utf-8",
    )

    async def fake_discover_entity(entity, *, followup_search=False, policy=None, tracer=None, exec_policy=None):
        candidate = DiscoveryCandidate(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            url="https://example.com/ar.pdf",
            title="AR 2024",
            source_type="official_site",
            source_tier="official",
            document_kind="official_pdf",
            confidence=0.9,
            reasons=["same_domain"],
            year=2024,
        )
        return DiscoveryRecord(entity=entity, status="success", candidates=[candidate], errors=[])

    monkeypatch.setattr(cli, "discover_entity", fake_discover_entity)

    result = runner.invoke(
        cli.app,
        ["discover", "--entities", str(entities_csv), "--workspace-root", str(workspace)],
    )
    assert result.exit_code == 0, result.output
    run_dir = next((workspace / "runs").glob("discover_*"))
    exec_policy_path = run_dir / "resolved_execution_policy.json"
    assert exec_policy_path.exists(), "resolved_execution_policy.json not found"
    data = json.loads(exec_policy_path.read_text(encoding="utf-8"))
    assert "allowed_command_stages" in data
    assert "download" in data
    assert "followup_search" in data


def test_review_emits_resolved_execution_policy(tmp_path: Path) -> None:
    """review command writes resolved_execution_policy.json."""
    workspace = tmp_path / "workspace"
    discover_dir = workspace / "runs" / "discover_seed"
    discover_dir.mkdir(parents=True)
    discover_json = discover_dir / "discover.json"
    discover_json.write_text(
        json.dumps([{
            "entity_id": "1001",
            "name": "Example Corp",
            "status": "success",
            "candidates": [{
                "entity_id": "1001",
                "entity_name": "Example Corp",
                "url": "https://example.com/ar.pdf",
                "title": "AR 2024",
                "source_tier": "official",
                "source_type": "official_site",
                "document_kind": "official_pdf",
                "confidence": 0.91,
                "reasons": ["same_domain"],
                "year": 2024,
            }],
        }]),
        encoding="utf-8",
    )
    result = runner.invoke(
        cli.app,
        ["review", "--input", str(discover_json), "--workspace-root", str(workspace)],
    )
    assert result.exit_code == 0, result.output
    run_dir = next((workspace / "runs").glob("review_*"))
    assert (run_dir / "resolved_execution_policy.json").exists()


# ---------------------------------------------------------------------------
# Stage 3: CLI blocked-stage enforcement
# ---------------------------------------------------------------------------

def test_cli_blocked_command_stage_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    """When discover is removed from allowed_command_stages, the CLI fails before any work."""
    workspace = tmp_path / "workspace"
    entities_csv = tmp_path / "entities.csv"
    entities_csv.write_text(
        "entity_id,name,ticker,official_website,cik,country\n1001,A,A,https://a.com,,US\n",
        encoding="utf-8",
    )

    # Write a restrictive execution policy that blocks discover.
    policy_file = tmp_path / "ep.yaml"
    policy_file.write_text(
        yaml.dump({
            "allowed_command_stages": ["review"],  # discover is NOT listed
            "allowed_source_families": ["*"],
            "download": {"enabled": True, "max_count": 50, "max_file_size_bytes": 52428800, "allowed_mime_types": ["*"]},
            "followup_search": {"enabled": True},
            "registry": {"root_restriction": "registry"},
        }),
        encoding="utf-8",
    )

    result = runner.invoke(
        cli.app,
        [
            "discover",
            "--entities", str(entities_csv),
            "--workspace-root", str(workspace),
            "--execution-policy-path", str(policy_file),
        ],
    )
    assert result.exit_code != 0 or "PolicyViolationError" in (result.output or "")


# ---------------------------------------------------------------------------
# Stage 3: LangGraph followup_node respects exec_policy
# ---------------------------------------------------------------------------

def test_langgraph_followup_node_blocked_by_exec_policy() -> None:
    """followup_node raises PolicyViolationError when exec_policy.followup_search.enabled=False."""
    from doc_workbench.orchestration.nodes import followup_node
    from doc_workbench.orchestration.state import WorkbenchState
    from doc_workbench.policy import load_context_policy

    exec_policy = _make_policy(followup_enabled=False)
    context_policy = load_context_policy()

    state: WorkbenchState = {
        "entities": [],
        "policy": context_policy,
        "exec_policy": exec_policy,
        "tracer": None,
        "output_dir": Path("."),
        "followup_search": True,
        "discovery_records": [],
    }

    with pytest.raises(PolicyViolationError, match="disabled"):
        followup_node(state)


def test_langgraph_followup_node_allowed_by_exec_policy() -> None:
    """followup_node proceeds normally when exec_policy.followup_search.enabled=True."""
    from doc_workbench.orchestration.nodes import followup_node
    from doc_workbench.orchestration.state import WorkbenchState
    from doc_workbench.policy import load_context_policy

    exec_policy = _make_policy(followup_enabled=True)
    context_policy = load_context_policy()

    state: WorkbenchState = {
        "entities": [],
        "policy": context_policy,
        "exec_policy": exec_policy,
        "tracer": None,
        "output_dir": Path("."),
        "followup_search": False,
        "discovery_records": [],
    }

    result = followup_node(state)
    assert "followup_records" in result
    assert result["followup_records"] == []


# ---------------------------------------------------------------------------
# Eval compat: evals still pass with execution policy loaded
# ---------------------------------------------------------------------------

def test_eval_runs_local_safe(tmp_path: Path) -> None:
    """Eval harness still passes after the execution policy layer is added."""
    report_path = tmp_path / "report.json"
    result = runner.invoke(
        cli.app,
        ["eval", "--report-path", str(report_path)],
    )
    assert result.exit_code == 0, result.output
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["aggregate"]["overall_passed"] is True


# ---------------------------------------------------------------------------
# New scenario tests (round 3 review findings)
# ---------------------------------------------------------------------------

def test_pointer_domain_blocked_before_resolve(monkeypatch) -> None:
    """Pointers to off-policy domains must be filtered out before resolve_pointer
    is called, preventing any network egress to blocked domains."""
    import asyncio
    import doc_workbench.acquisition.followup.workflow as workflow
    from doc_workbench.models import DiscoveryCandidate, EntityRecord

    resolve_calls: list[str] = []

    async def spy_resolve(pointer, exec_policy=None):
        resolve_calls.append(pointer.url)
        from doc_workbench.acquisition.followup.models import ResolvedTarget
        return ResolvedTarget(
            original_url=pointer.url,
            resolved_url=pointer.url,
            final_url=pointer.url,
            content_type="application/pdf",
            status_code=200,
            is_accessible=True,
            pointer=pointer,
        )

    html = b'<html><body><a href="https://blocked.evil/doc.pdf">Bad</a><a href="https://allowed.com/doc.pdf">Good</a></body></html>'

    async def fake_fetch(url: str, exec_policy=None):
        return html, "text/html", url

    monkeypatch.setattr(workflow, "_fetch_url", fake_fetch)
    monkeypatch.setattr(workflow, "resolve_pointer", spy_resolve)

    policy = _make_policy(allowed_source_families=["allowed.com"])
    entity = EntityRecord(entity_id="1", name="Test", ticker="", official_website="https://allowed.com", cik="", country="")
    seed = DiscoveryCandidate(
        entity_id="1", entity_name="Test", url="https://allowed.com/ir",
        title="IR", snippet="", source_type="search", source_tier="search_same_domain",
        document_kind="other", confidence=0.7, reasons=[], year=2024,
    )

    _results, _promoted = asyncio.run(
        workflow.run_followup_for_candidates(entity, [seed], materialize=False, registry=None, exec_policy=policy)
    )

    # resolve_pointer must only be called for the allowed domain.
    assert all("blocked.evil" not in url for url in resolve_calls), (
        f"resolve_pointer was called for blocked domain: {resolve_calls}"
    )


def test_registry_entity_id_path_traversal_blocked(tmp_path: Path) -> None:
    """A crafted entity_id containing path traversal sequences must be sanitized
    so no directory is ever created outside the registry root."""
    from doc_workbench.registry.document_registry import DocumentRegistry, _safe_path_component

    # Confirm the sanitizer neutralizes traversal tokens.
    assert _safe_path_component("../../escape") == "escape"
    assert _safe_path_component("../secret") == "secret"
    assert _safe_path_component("foo/../../bar") == "foo_.._.._bar"  # slashes → underscores

    registry_root = tmp_path / "registry"
    registry_root.mkdir()
    registry = DocumentRegistry(registry_root)

    # Registering with a traversal entity_id must succeed (sanitized), writing
    # inside the registry root — not outside it.
    result = registry.register_artifact(
        entity_id="../../escape",
        entity_name="Evil Corp",
        source_url="https://example.com/doc.pdf",
        artifact_family="annual_reports",
        artifact_type="document",
        year="2024",
        content_bytes=b"%PDF-1.4 fake",
        extension=".pdf",
        content_type="application/pdf",
    )
    # Confirm the written path is inside the registry root.
    assert result.local_path.resolve().is_relative_to(registry_root.resolve()), (
        f"Written path {result.local_path} escaped registry root {registry_root}"
    )
    # Confirm nothing was written directly to tmp_path root level as "escape/".
    escape_target = tmp_path / "escape"
    assert not escape_target.exists(), "Path traversal created directory outside registry root"


def test_langgraph_followup_node_no_followup_search_with_disabled_policy(monkeypatch) -> None:
    """followup_node must NOT raise PolicyViolationError when followup_search=False,
    even if exec_policy.followup_search.enabled is False."""
    from doc_workbench.orchestration import nodes
    from doc_workbench.models import DiscoveryRecord, EntityRecord
    from doc_workbench.policy import load_context_policy

    entity = EntityRecord(entity_id="1", name="Corp", ticker="", official_website="", cik="", country="")
    record = DiscoveryRecord(entity=entity, status="success", candidates=[], errors=[])
    policy = load_context_policy(None)
    exec_policy = _make_policy(followup_enabled=False)

    state = {
        "discovery_records": [record],
        "policy": policy,
        "exec_policy": exec_policy,
        "followup_search": False,   # follow-up NOT requested
        "tracer": None,
    }

    # Must not raise — follow-up is not being run.
    result = nodes.followup_node(state)
    assert "followup_records" in result


def test_parse_bool_rejects_quoted_false() -> None:
    """Quoted YAML strings like 'false' must raise ValueError, not become truthy.

    This guards against fail-open behaviour: a policy with download.enabled: "false"
    (a string) would be truthy under bool(), silently re-enabling downloads.
    """
    from doc_workbench.execution_policy import _parse_bool

    # Real booleans must pass through unchanged.
    assert _parse_bool(True, "download.enabled") is True
    assert _parse_bool(False, "followup_search.enabled") is False

    # Any non-bool type must raise ValueError — never silently coerce.
    import pytest
    with pytest.raises(ValueError, match="must be a boolean"):
        _parse_bool("false", "download.enabled")
    with pytest.raises(ValueError, match="must be a boolean"):
        _parse_bool("true", "followup_search.enabled")
    with pytest.raises(ValueError, match="must be a boolean"):
        _parse_bool(0, "download.enabled")
    with pytest.raises(ValueError, match="must be a boolean"):
        _parse_bool(1, "followup_search.enabled")


def test_load_execution_policy_rejects_quoted_bool(tmp_path: Path) -> None:
    """load_execution_policy must fail with a clear message when a boolean field
    is written as a quoted string in the YAML file."""
    import yaml
    import pytest
    from doc_workbench.execution_policy import load_execution_policy

    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        yaml.dump({
            "allowed_command_stages": ["download"],
            "allowed_source_families": ["annual_reports"],
            "download": {"enabled": "false", "max_count": 5},
            "followup_search": {"enabled": True},
            "registry": {},
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be a boolean"):
        load_execution_policy(policy_file)


def test_scan_non_pdf_artifact_returns_skipped(tmp_path: Path) -> None:
    """scan_pdf must return status=skipped for non-PDF content types instead of
    crashing with a pypdf exception."""
    from doc_workbench.registry.metadata_scanner import scan_pdf

    html_file = tmp_path / "artifact.html"
    html_file.write_bytes(b"<html><body>Annual Report</body></html>")

    result = scan_pdf(html_file, content_type="text/html")
    assert result["status"] == "skipped", f"Expected skipped, got {result}"
    assert "non_pdf" in result.get("modality", ""), f"Expected non_pdf modality: {result}"

    # Also confirm a .bin file is handled gracefully.
    bin_file = tmp_path / "artifact.bin"
    bin_file.write_bytes(b"\x00\x01\x02binary data")
    result_bin = scan_pdf(bin_file, content_type="application/octet-stream")
    assert result_bin["status"] == "skipped"
