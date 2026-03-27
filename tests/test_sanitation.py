from __future__ import annotations

from pathlib import Path


FORBIDDEN = [
    "".join(["Forward", " ", "Analytics"]),
    "".join(["forward", "analytics"]),
    "".join(["One", "Drive"]),
    "".join(["Company", " ", "Data", " ", "Extraction", " ", "Project"]),
]


def test_repo_does_not_include_internal_branding() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    text_files = [
        path
        for path in repo_root.rglob("*")
        if path.is_file() and path.suffix in {".md", ".py", ".toml", ".csv", ".json"}
    ]
    for path in text_files:
        content = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN:
            assert forbidden not in content, f"{forbidden} found in {path}"
