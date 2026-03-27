from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from doc_workbench.acquisition.discovery import (
    discover_entity,
    load_entities,
    write_discovery_artifacts,
)
from doc_workbench.acquisition.followup.workflow import (
    load_discovery_records,
    run_followup_for_candidates,
    write_followup_artifacts,
)
from doc_workbench.config import WorkspacePaths
from doc_workbench.models import DownloadRow, MetadataScanRow
from doc_workbench.registry.document_registry import DocumentRegistry
from doc_workbench.registry.metadata_scanner import scan_pdf
from doc_workbench.review.workflow import build_review_rows, write_review_csv
from doc_workbench.storage.downloader import download_bytes

app = typer.Typer(help="Public document acquisition workbench.", no_args_is_help=True)
console = Console()


@app.command("paths")
def show_paths(workspace_root: str | None = typer.Option(None, "--workspace-root")) -> None:
    paths = WorkspacePaths.resolve(workspace_root)
    paths.ensure()
    table = Table(title="Workspace Paths")
    table.add_column("Name")
    table.add_column("Path")
    table.add_row("root", str(paths.root))
    table.add_row("registry_root", str(paths.registry_root))
    table.add_row("runs_root", str(paths.runs_root))
    table.add_row("cache_root", str(paths.cache_root))
    console.print(table)


@app.command("discover")
def discover(
    entities: Path = typer.Option(Path("examples/public_companies.csv"), "--entities"),
    workspace_root: str | None = typer.Option(None, "--workspace-root"),
    followup_search: bool = typer.Option(False, "--followup-search/--no-followup-search"),
) -> None:
    paths = WorkspacePaths.resolve(workspace_root)
    paths.ensure()
    records = asyncio.run(_discover_all(load_entities(entities), followup_search=followup_search))
    output_dir, _run_id = paths.new_run_dir("discover")
    json_path, csv_path = write_discovery_artifacts(output_dir, records)
    console.print(f"Discovery JSON: {json_path}")
    console.print(f"Discovery summary: {csv_path}")


async def _discover_all(entities: list, *, followup_search: bool) -> list:
    return await asyncio.gather(
        *(discover_entity(entity, followup_search=followup_search) for entity in entities)
    )


@app.command("review")
def review(
    input_path: Path = typer.Option(..., "--input"),
    workspace_root: str | None = typer.Option(None, "--workspace-root"),
) -> None:
    paths = WorkspacePaths.resolve(workspace_root)
    paths.ensure()
    rows = build_review_rows(input_path)
    output_dir, _run_id = paths.new_run_dir("review")
    csv_path = write_review_csv(output_dir / "review_queue.csv", rows)
    console.print(f"Review queue: {csv_path}")


@app.command("download")
def download(
    input_path: Path = typer.Option(..., "--input"),
    workspace_root: str | None = typer.Option(None, "--workspace-root"),
) -> None:
    paths = WorkspacePaths.resolve(workspace_root)
    paths.ensure()
    output_dir, _run_id = paths.new_run_dir("download")
    registry = DocumentRegistry(paths.registry_root)
    rows = asyncio.run(_download_from_review(input_path, registry))
    json_path = output_dir / "download_results.json"
    csv_path = output_dir / "download_results.csv"
    json_path.write_text(json.dumps([row.to_dict() for row in rows], indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["document_id", "entity_id", "entity_name", "url", "local_path", "byte_size", "is_duplicate", "status", "error"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_dict())
    console.print(f"Download results: {json_path}")
    console.print(f"Registry root: {paths.registry_root}")


async def _download_from_review(input_path: Path, registry: DocumentRegistry) -> list[DownloadRow]:
    rows: list[DownloadRow] = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if str(row.get("recommendation") or "").strip() != "approved":
                continue
            url = str(row.get("url") or "")
            try:
                existing_followup_id = str(row.get("followup_target_document_id") or "").strip()
                if existing_followup_id:
                    existing_manifest = registry.get_manifest(existing_followup_id)
                else:
                    existing_manifest = None

                if existing_manifest is not None:
                    local_path = Path(str(existing_manifest["local_path"]))
                    pdf_bytes = local_path.read_bytes()
                    registration = registry.register_artifact(
                        entity_id=str(row.get("entity_id") or ""),
                        entity_name=str(row.get("entity_name") or ""),
                        source_url=url,
                        artifact_family="annual_reports",
                        artifact_type=str(row.get("candidate_kind") or "document"),
                        year=str(row.get("year") or "unknown"),
                        content_bytes=pdf_bytes,
                        extension=local_path.suffix or ".pdf",
                        content_type=str(existing_manifest.get("content_type") or "application/pdf"),
                        stage="final",
                        source_parent_document_id=str(existing_manifest.get("document_id") or ""),
                        parsed=dict(existing_manifest.get("parsed") or {}),
                        metadata=dict(existing_manifest.get("metadata") or {}),
                        dedupe_scope="family",
                    )
                else:
                    pdf_bytes = await download_bytes(url)
                    registration = registry.register_document(
                        entity_id=str(row.get("entity_id") or ""),
                        entity_name=str(row.get("entity_name") or ""),
                        source_url=url,
                        family="annual_reports",
                        doc_type=str(row.get("candidate_kind") or "document"),
                        year=str(row.get("year") or "unknown"),
                        pdf_bytes=pdf_bytes,
                    )
                rows.append(
                    DownloadRow(
                        document_id=registration.document_id,
                        entity_id=str(row.get("entity_id") or ""),
                        entity_name=str(row.get("entity_name") or ""),
                        url=url,
                        local_path=str(registration.local_path),
                        byte_size=len(pdf_bytes),
                        is_duplicate=registration.is_duplicate,
                        status="complete",
                    )
                )
            except Exception as exc:
                rows.append(
                    DownloadRow(
                        document_id="",
                        entity_id=str(row.get("entity_id") or ""),
                        entity_name=str(row.get("entity_name") or ""),
                        url=url,
                        local_path="",
                        byte_size=0,
                        is_duplicate=False,
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
    return rows


@app.command("followup-search")
def followup_search(
    input_path: Path = typer.Option(..., "--input"),
    workspace_root: str | None = typer.Option(None, "--workspace-root"),
) -> None:
    paths = WorkspacePaths.resolve(workspace_root)
    paths.ensure()
    registry = DocumentRegistry(paths.registry_root)
    output_dir, _run_id = paths.new_run_dir("followup_search")
    records = load_discovery_records(input_path)
    results_by_entity: dict[str, list] = {}
    promoted_candidates = []
    enriched_records = []
    for record in asyncio.run(_followup_all(records, registry)):
        enriched_records.append(record["record"])
        results_by_entity[record["entity_id"]] = record["results"]
        promoted_candidates.extend(record["promoted"])
    results_path, promoted_json_path, enriched_path = write_followup_artifacts(
        output_dir,
        results_by_entity=results_by_entity,
        promoted_candidates=promoted_candidates,
        enriched_records=enriched_records,
    )
    console.print(f"Follow-up results: {results_path}")
    console.print(f"Promoted candidates: {promoted_json_path}")
    console.print(f"Enriched discovery: {enriched_path}")


async def _followup_all(records: list, registry: DocumentRegistry) -> list[dict]:
    output: list[dict] = []
    for record in records:
        seed_candidates = [
            candidate
            for candidate in record.candidates
            if candidate.source_type == "search" or candidate.source_tier.startswith("search_")
        ]
        results, promoted = await run_followup_for_candidates(
            record.entity,
            seed_candidates,
            materialize=True,
            registry=registry,
        )
        deduped: dict[str, object] = {}
        for candidate in [*record.candidates, *promoted]:
            existing = deduped.get(candidate.url)
            if existing is None or candidate.confidence > existing.confidence:
                deduped[candidate.url] = candidate
        record.candidates = sorted(deduped.values(), key=lambda item: item.confidence, reverse=True)
        output.append(
            {
                "entity_id": record.entity.entity_id,
                "record": record,
                "results": results,
                "promoted": promoted,
            }
        )
    return output


@app.command("scan")
def scan(
    entity_id: str = typer.Option("", "--entity-id"),
    all_: bool = typer.Option(False, "--all"),
    workspace_root: str | None = typer.Option(None, "--workspace-root"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    if not entity_id and not all_:
        raise typer.BadParameter("Pass --all or --entity-id.")
    paths = WorkspacePaths.resolve(workspace_root)
    paths.ensure()
    registry = DocumentRegistry(paths.registry_root)
    manifests = registry.list_manifests(entity_id or None, artifact_family="annual_reports")
    output_dir, _run_id = paths.new_run_dir("scan")
    rows: list[MetadataScanRow] = []
    for manifest in manifests:
        if not force and ((manifest.get("pipeline_status") or {}).get("metadata_scan_status") == "complete"):
            continue
        result = scan_pdf(Path(str(manifest["local_path"])))
        updated = registry.update_manifest(
            str(manifest["document_id"]),
            {
                "metadata": result,
                "pipeline_status": {"metadata_scan_status": result["status"]},
            },
        )
        rows.append(
            MetadataScanRow(
                document_id=str(updated["document_id"]),
                entity_id=str(updated["entity_id"]),
                entity_name=str(updated["entity_name"]),
                title=str((updated.get("metadata") or {}).get("title") or ""),
                issuer_name=str((updated.get("metadata") or {}).get("issuer_name") or ""),
                reporting_period=str((updated.get("metadata") or {}).get("reporting_period") or ""),
                publication_date=str((updated.get("metadata") or {}).get("publication_date") or ""),
                page_count=(updated.get("metadata") or {}).get("page_count"),
                modality=str((updated.get("metadata") or {}).get("modality") or ""),
                status=str((updated.get("pipeline_status") or {}).get("metadata_scan_status") or ""),
                error=str((updated.get("metadata") or {}).get("error") or ""),
            )
        )
    json_path = output_dir / "scan_results.json"
    json_path.write_text(json.dumps([row.to_dict() for row in rows], indent=2), encoding="utf-8")
    console.print(f"Metadata scan results: {json_path}")
