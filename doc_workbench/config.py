from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


VALID_ENGINES = ("legacy", "langgraph")


def resolve_engine(cli_override: str | None = None) -> str:
    """Return the engine to use: explicit flag > env var > 'legacy'.

    Raises ``ValueError`` on any unrecognised value so typos fail fast rather
    than silently running the wrong path.
    """
    if cli_override is not None:
        if cli_override not in VALID_ENGINES:
            raise ValueError(
                f"Unknown engine {cli_override!r}. Valid choices: {VALID_ENGINES}"
            )
        return cli_override
    env = os.environ.get("DOC_WORKBENCH_ENGINE", "").strip().lower()
    if env:
        if env not in VALID_ENGINES:
            raise ValueError(
                f"DOC_WORKBENCH_ENGINE={env!r} is not a recognised engine. "
                f"Valid choices: {VALID_ENGINES}"
            )
        return env
    return "legacy"


def slugify(value: str) -> str:
    output = []
    last_dash = False
    for char in value.lower():
        if char.isalnum():
            output.append(char)
            last_dash = False
            continue
        if not last_dash:
            output.append("-")
            last_dash = True
    return "".join(output).strip("-") or "entity"


@dataclass(slots=True)
class WorkspacePaths:
    root: Path
    registry_root: Path
    runs_root: Path
    cache_root: Path
    traces_root: Path

    @classmethod
    def resolve(cls, workspace_root: str | None = None) -> "WorkspacePaths":
        root = Path(
            workspace_root
            or os.environ.get("DOC_WORKBENCH_HOME")
            or (Path.cwd() / "workspace")
        ).expanduser()
        return cls(
            root=root,
            registry_root=root / "registry",
            runs_root=root / "runs",
            cache_root=root / "cache",
            traces_root=root / "traces",
        )

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry_root.mkdir(parents=True, exist_ok=True)
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.traces_root.mkdir(parents=True, exist_ok=True)

    def new_run_dir(self, name: str) -> tuple[Path, str]:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        run_id = f"{name}_{stamp}"
        output_dir = self.runs_root / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir, run_id
