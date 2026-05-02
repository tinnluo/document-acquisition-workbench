from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doc_workbench.config import slugify
from doc_workbench.models import RegistrationResult

# Characters that are safe in a single path component.
# Strips OS path separators, null bytes, and common traversal patterns.
_SAFE_COMPONENT_RE = re.compile(r"[^\w\-. ]")


def _safe_path_component(value: str, max_len: int = 80) -> str:
    """Sanitize *value* for use as a single filesystem path component.

    Removes characters that could introduce path traversal
    (``/``, ``\\``, ``..``, null bytes, etc.) and truncates to *max_len*.
    """
    # Replace any separator-like character with underscore.
    sanitized = value.replace("/", "_").replace("\\", "_").replace("\x00", "")
    # Remove any remaining characters that aren't word chars, dash, dot, or space.
    sanitized = _SAFE_COMPONENT_RE.sub("_", sanitized)
    # Collapse repeated underscores and strip leading/trailing dots (hidden-file risk).
    sanitized = re.sub(r"_+", "_", sanitized).strip("._")
    return (sanitized or "entity")[:max_len]


class DocumentRegistry:
    def __init__(self, registry_root: Path, exec_policy: Any = None) -> None:
        self.registry_root = registry_root
        self.registry_root.mkdir(parents=True, exist_ok=True)
        self._exec_policy = exec_policy

    def _entity_root(self, entity_id: str, entity_name: str) -> Path:
        safe_id = _safe_path_component(entity_id)
        safe_name = slugify(entity_name)
        root = self.registry_root / f"{safe_id}_{safe_name}"
        # Validate that the entity root is inside registry_root before creating it.
        try:
            root.resolve().relative_to(self.registry_root.resolve())
        except ValueError:
            raise ValueError(
                f"entity_id {entity_id!r} resolves outside registry root. "
                "Path traversal attempt blocked."
            )
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _iter_manifest_paths(self, entity_id: str | None = None) -> list[Path]:
        if not self.registry_root.exists():
            return []
        if entity_id:
            # Normalize entity_id the same way _entity_root() does so that IDs
            # containing '/', '..', spaces, or other sanitized characters match
            # the directory name that was produced at write time.
            safe_id = _safe_path_component(entity_id)
            roots = [
                root
                for root in self.registry_root.iterdir()
                if root.is_dir() and root.name.startswith(f"{safe_id}_")
            ]
        else:
            roots = [root for root in self.registry_root.iterdir() if root.is_dir()]
        registry_resolved = self.registry_root.resolve()
        manifest_paths: list[Path] = []
        for root in roots:
            for p in root.rglob("metadata.json"):
                # Resolve symlinks before any I/O to block manifest files that
                # point (via symlink or traversal) outside the registry root.
                resolved = p.resolve()
                try:
                    resolved.relative_to(registry_resolved)
                except ValueError:
                    continue  # silently skip; attacker-controlled path ignored
                manifest_paths.append(resolved)
        return manifest_paths

    def register_artifact(
        self,
        *,
        entity_id: str,
        entity_name: str,
        source_url: str,
        artifact_family: str,
        artifact_type: str,
        year: str,
        content_bytes: bytes,
        extension: str,
        content_type: str,
        stage: str = "pre_review",
        source_parent_document_id: str = "",
        parsed: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        dedupe_scope: str = "entity",
    ) -> RegistrationResult:
        entity_root = self._entity_root(entity_id, entity_name)
        incoming_hash = hashlib.sha256(content_bytes).hexdigest()

        # Use the symlink-safe, registry-root-validated iterator so that
        # poisoned or symlinked metadata.json files cannot influence dedupe
        # results or return untrusted local_path values.
        for manifest_path in self._iter_manifest_paths(entity_id):
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            # Guard against entity_id collisions: two distinct entity IDs can
            # sanitize to the same directory prefix (e.g. "a/b" and "a_b" both
            # become "a_b").  Verify the raw entity_id stored in the manifest
            # matches the caller's entity_id before treating the entry as a
            # duplicate — otherwise unrelated entities in the same workspace
            # can cross-wire dedupe and reuse each other's artifacts.
            if payload.get("entity_id") != entity_id:
                continue
            if dedupe_scope == "family" and payload.get("artifact_family") != artifact_family:
                continue
            if payload.get("source_url") == source_url or payload.get("content_hash") == incoming_hash:
                existing_local_path = Path(str(payload["local_path"]))
                # Validate the stored local_path is still inside registry_root
                # before returning it as authoritative — a tampered manifest
                # could otherwise redirect callers to an out-of-root path.
                registry_resolved = self.registry_root.resolve()
                try:
                    existing_local_path.resolve().relative_to(registry_resolved)
                except ValueError:
                    # Manifest has been tampered; skip this entry and fall
                    # through to a fresh registration.
                    continue
                return RegistrationResult(
                    document_id=str(payload["document_id"]),
                    document_folder=manifest_path.parent,
                    local_path=existing_local_path,
                    is_duplicate=True,
                )

        document_id = f"doc_{incoming_hash[:12]}"
        # Sanitize every component that comes from external input before
        # composing the filesystem path.  _safe_path_component() strips
        # separators, null bytes, and traversal sequences, so crafted
        # artifact_family/year/artifact_type values cannot escape entity_root.
        safe_family = _safe_path_component(artifact_family)
        safe_year = _safe_path_component(year)
        safe_type = _safe_path_component(artifact_type)
        # Clamp extension to a simple ".suffix" with no separators.
        raw_ext = extension if extension.startswith(".") else f".{extension}"
        clean_extension = "." + _safe_path_component(raw_ext.lstrip("."), max_len=10)
        artifact_folder = entity_root / safe_family / safe_year / safe_type / document_id
        local_path = artifact_folder / f"artifact{clean_extension}"

        # Enforce containment unconditionally — regardless of exec_policy — so
        # callers that omit exec_policy still cannot escape the registry root.
        try:
            artifact_folder.resolve().relative_to(self.registry_root.resolve())
        except ValueError:
            raise ValueError(
                f"Computed artifact path {artifact_folder} escapes registry root. "
                "Path traversal attempt blocked."
            )

        # Also apply the execution-policy registry-root check (redundant but
        # kept so the policy violation is surfaced as PolicyViolationError).
        if self._exec_policy is not None:
            from doc_workbench.execution_policy import enforce_registry_root
            enforce_registry_root(self._exec_policy, local_path, self.registry_root.parent)

        artifact_folder.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content_bytes)

        manifest = {
            "document_id": document_id,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "source_url": source_url,
            "artifact_family": artifact_family,
            "artifact_type": artifact_type,
            "stage": stage,
            "year": year,
            "content_hash": incoming_hash,
            "content_type": content_type,
            "source_parent_document_id": source_parent_document_id,
            "parsed": parsed or {},
            "metadata": metadata or {},
            "pipeline_status": {
                "download_status": "complete",
                "metadata_scan_status": "pending",
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "local_path": str(local_path),
        }
        (artifact_folder / "metadata.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return RegistrationResult(
            document_id=document_id,
            document_folder=artifact_folder,
            local_path=local_path,
            is_duplicate=False,
        )

    def register_document(
        self,
        *,
        entity_id: str,
        entity_name: str,
        source_url: str,
        family: str,
        doc_type: str,
        year: str,
        pdf_bytes: bytes,
        content_type: str = "application/pdf",
        extension: str = ".pdf",
    ) -> RegistrationResult:
        # Derive extension from content_type if caller passes the real MIME.
        if extension == ".pdf" and content_type and "pdf" not in content_type.lower():
            if "html" in content_type.lower():
                extension = ".html"
            elif content_type and content_type != "application/pdf":
                extension = ".bin"
        # Only promote to "final" when the content is a PDF.  Non-PDF responses
        # (HTML landing pages, binaries) go to "pre_review" so they cannot be
        # treated as completed annual-report artifacts by downstream consumers.
        is_pdf = "pdf" in content_type.lower()
        stage = "final" if (family == "annual_reports" and is_pdf) else "pre_review"
        return self.register_artifact(
            entity_id=entity_id,
            entity_name=entity_name,
            source_url=source_url,
            artifact_family=family,
            artifact_type=doc_type,
            year=year,
            content_bytes=pdf_bytes,
            extension=extension,
            content_type=content_type,
            stage=stage,
            dedupe_scope="family",
        )

    def list_manifests(
        self,
        entity_id: str | None = None,
        artifact_family: str | None = None,
    ) -> list[dict[str, Any]]:
        manifests: list[dict[str, Any]] = []
        for manifest_path in self._iter_manifest_paths(entity_id):
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if artifact_family and payload.get("artifact_family") != artifact_family:
                continue
            manifests.append(payload)
        return manifests

    def get_manifest(self, document_id: str) -> dict[str, Any] | None:
        for manifest_path in self._iter_manifest_paths():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if payload.get("document_id") == document_id:
                return payload
        return None

    def find_by_source_url(self, source_url: str) -> dict[str, Any] | None:
        for manifest_path in self._iter_manifest_paths():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if payload.get("source_url") == source_url:
                return payload
        return None

    def update_manifest(self, document_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        for manifest_path in self._iter_manifest_paths():
            # _iter_manifest_paths() already resolves symlinks and validates that
            # every returned path lives inside registry_root, so the read here is safe.
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if payload.get("document_id") != document_id:
                continue
            for key, value in updates.items():
                if isinstance(value, dict) and isinstance(payload.get(key), dict):
                    payload[key].update(value)
                else:
                    payload[key] = value
            manifest_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return payload
        raise KeyError(f"Unknown document_id: {document_id}")
