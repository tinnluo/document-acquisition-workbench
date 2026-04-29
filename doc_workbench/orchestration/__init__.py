"""LangGraph orchestration layer for the document acquisition workflow.

Eager imports are intentionally omitted from this __init__ so that importing
sub-modules (e.g. ``doc_workbench.orchestration.nodes``) does not transitively
import ``graph.py``, which requires the optional ``langgraph`` package.

Callers that need the full graph should import directly:
    from doc_workbench.orchestration.graph import run_graph
    from doc_workbench.orchestration.state import WorkbenchState
"""
