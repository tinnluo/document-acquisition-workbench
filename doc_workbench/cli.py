from __future__ import annotations

import asyncio
import csv
import json
import click
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from doc_workbench.acquisition.discovery import (
    build_ranking_trace,
    discover_entity,
    load_entities,
    write_discovery_artifacts,
)
from doc_workbench.acquisition.followup.workflow import (
    load_discovery_records,
    run_followup_for_candidates,
    write_followup_artifacts,
)
from doc_workbench.config import VALID_ENGINES, WorkspacePaths, resolve_engine
from doc_workbench.models import DownloadRow, MetadataScanRow
from doc_workbench.observability.tracer import RunTrace, summarize_trace
from doc_workbench.policy import load_context_policy, write_resolved_policy
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
    table.add_row("traces_root", str(paths.traces_root))
    console.print(table)


@app.command("policy")
def show_policy(policy_path: str | None = typer.Option(None, "--policy-path")) -> None:
    policy = load_context_policy(policy_path)
    console.print_json(json.dumps({"policy_digest": policy.digest, **policy.to_dict()}, indent=2))


@app.command("trace-summary")
def trace_summary(
    input_path: Path = typer.Option(..., "--input"),
) -> None:
    summary = summarize_trace(input_path)
    console.print_json(json.dumps(summary, indent=2))


@app.command("discover")
def discover(
    entities: Path = typer.Option(Path("examples/public_companies.csv"), "--entities"),
    workspace_root: str | None = typer.Option(None, "--workspace-root"),
    followup_search: bool = typer.Option(False, "--followup-search/--no-followup-search"),
    policy_path: str | None = typer.Option(None, "--policy-path"),
    engine: str | None = typer.Option(None, "--engine", help="Engine to use.", click_type=click.Choice(list(VALID_ENGINES))),
) -> None:
    paths = WorkspacePaths.resolve(workspace_root)
    paths.ensure()
    policy = load_context_policy(policy_path)
    try:
        selected_engine = resolve_engine(engine)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="'--engine' / DOC_WORKBENCH_ENGINE") from exc
    output_dir, run_id = paths.new_run_dir("discover")
    tracer = RunTrace(trace_id=run_id, run_id=run_id, command="discover", policy_digest=policy.digest)

    if selected_engine == "langgraph":
        try:
            from doc_workbench.orchestration.graph import run_graph
        except ImportError as exc:
            raise click.UsageError(
                "The langgraph engine requires the '[orchestration]' optional extra.\n"
                "Install it with:  pip install -e '.[orchestration]'"
            ) from exc

        entity_list = load_entities(entities)
        final_state = run_graph(
            entities=entity_list,
            policy=policy,
            tracer=tracer,
            output_dir=output_dir,
            followup_search=followup_search,
        )
        records = final_state.get("ranked_records") or final_state.get("discovery_records", [])
    else:
        records = asyncio.run(_discover_all(load_entities(entities), followup_search=followup_search, policy=policy, tracer=tracer))

    json_path, csv_path = write_discovery_artifacts(output_dir, records)
    ranking_trace_path = output_dir / "ranking_trace.json"
    ranking_trace_path.write_text(json.dumps(build_ranking_trace(records, policy), indent=2), encoding="utf-8")
    write_resolved_policy(output_dir / "resolved_policy.json", policy)
    trace_path = tracer.write(paths.traces_root / f"{run_id}.json")
    console.print(f"Engine: {selected_engine}")
    console.print(f"Discovery JSON: {json_path}")
    console.print(f"Discovery summary: {csv_path}")
    console.print(f"Resolved policy: {output_dir / 'resolved_policy.json'}")
    console.print(f"Ranking trace: {ranking_trace_path}")
    console.print(f"Trace file: {trace_path}")


async def _discover_all(entities: list, *, followup_search: bool, policy, tracer: RunTrace) -> list:
    records = []
    for entity in entities:
        records.append(
            await discover_entity(
                entity,
                followup_search=followup_search,
                policy=policy,
                tracer=tracer,
            )
        )
    return records


@app.command("review")
def review(
    input_path: Path = typer.Option(..., "--input"),
    workspace_root: str | None = typer.Option(None, "--workspace-root"),
    policy_path: str | None = typer.Option(None, "--policy-path"),
    engine: str | None = typer.Option(None, "--engine", help="Engine to use.", click_type=click.Choice(list(VALID_ENGINES))),
) -> None:
    paths = WorkspacePaths.resolve(workspace_root)
    paths.ensure()
    policy = load_context_policy(policy_path)
    try:
        selected_engine = resolve_engine(engine)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="'--engine' / DOC_WORKBENCH_ENGINE") from exc
    output_dir, run_id = paths.new_run_dir("review")
    tracer = RunTrace(trace_id=run_id, run_id=run_id, command="review", policy_digest=policy.digest)

    if selected_engine == "langgraph":
        from doc_workbench.acquisition.followup.workflow import load_discovery_records as _load
        from doc_workbench.orchestration.nodes import rank_node, review_prep_node
        from doc_workbench.orchestration.state import WorkbenchState

        records = _load(input_path)
        # NOTE: review --engine langgraph calls rank_node + review_prep_node directly.
        # It does NOT execute a compiled StateGraph — review operates on an existing
        # discovery file, so running the full graph would redundantly re-run discover
        # and followup stages.  The [orchestration] extra (langgraph package) is NOT
        # required for this path; only doc_workbench.orchestration.nodes is imported.
        rank_state: WorkbenchState = {
            "entities": [],
            "policy": policy,
            "tracer": tracer,
            "output_dir": output_dir,
            "followup_search": False,
            "followup_records": records,
        }
        rank_result = rank_node(rank_state)
        review_state: WorkbenchState = {**rank_state, **rank_result}
        result = review_prep_node(review_state)
        rows = result["review_rows"]
        review_trace = result["review_trace"]
        recommendation_summary = result["recommendation_summary"]
    else:
        rows, review_trace, recommendation_summary = build_review_rows(input_path, policy)

    csv_path = write_review_csv(output_dir / "review_queue.csv", rows)
    review_trace_path = output_dir / "review_trace.json"
    review_trace_path.write_text(json.dumps(review_trace, indent=2), encoding="utf-8")
    write_resolved_policy(output_dir / "resolved_policy.json", policy)
    # review_prep_node already emits this span (with real latency) in the
    # langgraph path — only add it here for the legacy path to avoid doubling.
    if selected_engine != "langgraph":
        tracer.add_span(
            entity_id="all",
            stage="review_queue_generation",
            provider="review_policy",
            latency_ms=0.0,
            candidate_count_in=len(review_trace),
            candidate_count_out=len(rows),
            recommendation_summary=recommendation_summary,
        )
    trace_path = tracer.write(paths.traces_root / f"{run_id}.json")
    console.print(f"Engine: {selected_engine}")
    console.print(f"Review queue: {csv_path}")
    console.print(f"Review trace: {review_trace_path}")
    console.print(f"Resolved policy: {output_dir / 'resolved_policy.json'}")
    console.print(f"Trace file: {trace_path}")


@app.command("download")
def download(
    input_path: Path = typer.Option(..., "--input"),
    workspace_root: str | None = typer.Option(None, "--workspace-root"),
) -> None:
    paths = WorkspacePaths.resolve(workspace_root)
    paths.ensure()
    output_dir, run_id = paths.new_run_dir("download")
    tracer = RunTrace(trace_id=run_id, run_id=run_id, command="download", policy_digest="")
    registry = DocumentRegistry(paths.registry_root)
    start = time.perf_counter()
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
    tracer.add_span(
        entity_id="all",
        stage="download_documents",
        provider="registry_downloader",
        latency_ms=(time.perf_counter() - start) * 1000.0,
        candidate_count_in=len(rows),
        candidate_count_out=sum(1 for row in rows if row.status == "complete"),
    )
    trace_path = tracer.write(paths.traces_root / f"{run_id}.json")
    console.print(f"Download results: {json_path}")
    console.print(f"Registry root: {paths.registry_root}")
    console.print(f"Trace file: {trace_path}")


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
                existing_manifest = registry.get_manifest(existing_followup_id) if existing_followup_id else None
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
    policy_path: str | None = typer.Option(None, "--policy-path"),
) -> None:
    paths = WorkspacePaths.resolve(workspace_root)
    paths.ensure()
    policy = load_context_policy(policy_path)
    registry = DocumentRegistry(paths.registry_root)
    output_dir, run_id = paths.new_run_dir("followup_search")
    tracer = RunTrace(trace_id=run_id, run_id=run_id, command="followup-search", policy_digest=policy.digest)
    records = load_discovery_records(input_path)
    results_by_entity: dict[str, list] = {}
    promoted_candidates = []
    enriched_records = []
    for record in asyncio.run(_followup_all(records, registry, policy, tracer)):
        enriched_records.append(record["record"])
        results_by_entity[record["entity_id"]] = record["results"]
        promoted_candidates.extend(record["promoted"])
    results_path, promoted_json_path, enriched_path = write_followup_artifacts(
        output_dir,
        results_by_entity=results_by_entity,
        promoted_candidates=promoted_candidates,
        enriched_records=enriched_records,
    )
    write_resolved_policy(output_dir / "resolved_policy.json", policy)
    trace_path = tracer.write(paths.traces_root / f"{run_id}.json")
    console.print(f"Follow-up results: {results_path}")
    console.print(f"Promoted candidates: {promoted_json_path}")
    console.print(f"Enriched discovery: {enriched_path}")
    console.print(f"Resolved policy: {output_dir / 'resolved_policy.json'}")
    console.print(f"Trace file: {trace_path}")


async def _followup_all(records: list, registry: DocumentRegistry, policy, tracer: RunTrace) -> list[dict]:
    output: list[dict] = []
    approval_cutoff = policy.review_thresholds.approved_min_confidence
    for record in records:
        seed_candidates = [
            candidate
            for candidate in record.candidates
            if (candidate.source_type == "search" or candidate.source_tier.startswith("search_"))
            and candidate.source_tier in policy.followup_search.allowed_seed_source_tiers
        ]
        has_higher_priority_candidate = any(
            candidate.source_tier in {"official", "regulatory"} and candidate.confidence >= approval_cutoff
            for candidate in record.candidates
        )
        enabled = not (policy.followup_search.skip_if_higher_priority_approved and has_higher_priority_candidate)
        start = time.perf_counter()
        if enabled:
            results, promoted = await run_followup_for_candidates(
                record.entity,
                seed_candidates,
                materialize=True,
                registry=registry,
            )
        else:
            results, promoted = [], []
        deduped: dict[str, object] = {}
        for candidate in [*record.candidates, *promoted]:
            existing = deduped.get(candidate.url)
            if existing is None or candidate.confidence > existing.confidence:
                deduped[candidate.url] = candidate
        record.candidates = sorted(deduped.values(), key=lambda item: item.confidence, reverse=True)
        top = record.candidates[0] if record.candidates else None
        tracer.add_span(
            entity_id=record.entity.entity_id,
            stage="followup_extraction",
            provider="followup_search",
            latency_ms=(time.perf_counter() - start) * 1000.0,
            candidate_count_in=len(seed_candidates),
            candidate_count_out=len(promoted),
            top_candidate_url=top.url if top else "",
            top_confidence=float(top.confidence) if top else 0.0,
            details={"enabled": enabled, "skip_due_to_policy": has_higher_priority_candidate},
        )
        output.append(
            {
                "entity_id": record.entity.entity_id,
                "record": record,
                "results": results,
                "promoted": promoted,
            }
        )
    return output


@app.command("eval")
def run_eval(
    fixtures_dir: Path = typer.Option(None, "--fixtures-dir", help="Override fixture directory (default: bundled package fixtures)"),
    report_path: Path = typer.Option(Path("evals/latest_report.json"), "--report-path"),
) -> None:
    """Run the eval harness against fixture cases and write a machine-readable report."""
    from doc_workbench.evals.run_evals import FIXTURES_DIR as _DEFAULT_FIXTURES, run_evals

    effective_fixtures = fixtures_dir if fixtures_dir is not None else _DEFAULT_FIXTURES
    try:
        report = run_evals(fixtures_dir=effective_fixtures, report_path=report_path)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc), param_hint="--fixtures-dir") from exc
    agg = report["aggregate"]
    console.print(f"Evals: {agg['passed']}/{agg['total_cases']} passed  (pass_rate={agg['pass_rate']})")
    console.print(f"Report written to: {report_path}")
    if not agg["overall_passed"]:
        for case in report["cases"]:
            if not case["passed"]:
                console.print(f"  FAIL  {case['entity_id']}  actual={case['actual']}  expected={case['expected']}")
        raise typer.Exit(code=1)


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
    output_dir, run_id = paths.new_run_dir("scan")
    tracer = RunTrace(trace_id=run_id, run_id=run_id, command="scan", policy_digest="")
    rows: list[MetadataScanRow] = []
    start = time.perf_counter()
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
    tracer.add_span(
        entity_id=entity_id or "all",
        stage="metadata_scan",
        provider="metadata_scanner",
        latency_ms=(time.perf_counter() - start) * 1000.0,
        candidate_count_in=len(manifests),
        candidate_count_out=len(rows),
    )
    trace_path = tracer.write(paths.traces_root / f"{run_id}.json")
    console.print(f"Metadata scan results: {json_path}")
    console.print(f"Trace file: {trace_path}")
