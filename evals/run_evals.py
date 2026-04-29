#!/usr/bin/env python3
"""Source-tree shim — delegates to the installed package eval runner.

For installed users, prefer:
    doc-workbench eval
    python -m doc_workbench.evals.run_evals

This file exists only so that contributors working inside the repo can still
run ``python evals/run_evals.py`` without installing the package first.

Fixtures are stored in ``doc_workbench/evals/fixtures/`` (the package canonical
location) and resolved relative to that file, so they work after a wheel install
and from this source-tree shim alike.  There is no separate ``evals/fixtures/``
directory — do not create one; edit ``doc_workbench/evals/fixtures/`` instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repo root is on sys.path when run directly from the source tree.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from doc_workbench.evals.run_evals import FIXTURES_DIR, run_evals  # noqa: F401,E402

if __name__ == "__main__":
    from doc_workbench.evals.run_evals import main
    main()
