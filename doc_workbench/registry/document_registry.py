from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doc_workbench.config import slugify
from doc_workbench.models import RegistrationResult


class DocumentRegistry:
    def __init__(self, registry_root: Path) -> None:
        self.registry_root = registry_root
        self.registry_root.mkdir(parents=True, exist_ok=True)

    def _entity_root(self, entity_id: str, entity_name: str) -> Path:
        root = self.registry_root / f"{entity_id}_{slugify(entity_name)}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _iter_manifest_paths(self, entity_id: str | None = None) -> list[Path]:
        if not self.registry_root.exists():
            return []
        if entity_id:
            roots = [
                root
                for root in self.registry_root.iterdir()
                if root.is_dir() and root.name.startswith(f"{entity_id}_")
            ]
        else:
            roots = [root for root in self.registry_root.iterdir() if root.is_dir()]
        manifest_paths: list[Path] = []
        for root in roots:
            manifest_paths.extend(root.rglob("metadata.json"))
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

        for manifest_path in entity_root.rglob("metadata.json"):
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if dedupe_scope == "family" and payload.get("artifact_family") != artifact_family:
                continue
            if payload.get("source_url") == source_url or payload.get("content_hash") == incoming_hash:
                existing_local_path = Path(str(payload["local_path"]))
                return RegistrationResult(
                    document_id=str(payload["document_id"]),
                    document_folder=manifest_path.parent,
                    local_path=existing_local_path,
                    is_duplicate=True,
                )

        document_id = f"doc_{incoming_hash[:12]}"
        artifact_folder = entity_root / artifact_family / year / artifact_type / document_id
        artifact_folder.mkdir(parents=True, exist_ok=True)
        clean_extension = extension if extension.startswith(".") else f".{extension}"
        local_path = artifact_folder / f"artifact{clean_extension}"
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
    ) -> RegistrationResult:
        return self.register_artifact(
            entity_id=entity_id,
            entity_name=entity_name,
            source_url=source_url,
            artifact_family=family,
            artifact_type=doc_type,
            year=year,
            content_bytes=pdf_bytes,
            extension=".pdf",
            content_type="application/pdf",
            stage="final" if family == "annual_reports" else "pre_review",
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
