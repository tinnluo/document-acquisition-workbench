from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


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
        )

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry_root.mkdir(parents=True, exist_ok=True)
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def new_run_dir(self, name: str) -> tuple[Path, str]:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        run_id = f"{name}_{stamp}"
        output_dir = self.runs_root / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir, run_id
