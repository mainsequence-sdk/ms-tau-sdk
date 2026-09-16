from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MARKDOWN_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def _active_documents() -> list[Path]:
    documents = [REPOSITORY_ROOT / "README.md", REPOSITORY_ROOT / "CHANGELOG.md"]
    for path in (REPOSITORY_ROOT / "docs").rglob("*.md"):
        relative = path.relative_to(REPOSITORY_ROOT / "docs")
        if relative.parts[0] in {"history", "migration"}:
            continue
        if path.name.startswith("adr-56-"):
            continue
        documents.append(path)
    return documents


def test_active_documentation_local_links_resolve() -> None:
    missing: list[str] = []
    for document in _active_documents():
        for target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            path_text = target.split("#", 1)[0]
            if not path_text or "://" in path_text:
                continue
            target_path = (document.parent / path_text).resolve()
            if not target_path.exists():
                missing.append(f"{document.relative_to(REPOSITORY_ROOT)} -> {target}")

    assert missing == []


def test_active_adr_index_lists_the_complete_sdk_decision_set() -> None:
    active_adrs = {
        path.name for path in (REPOSITORY_ROOT / "docs/adrs").glob("[0-9][0-9][0-9][0-9]-*.md")
    }

    assert active_adrs == {
        "0001-workspace-bound-python-sdk.md",
        "0002-runtime-and-protocol-contracts.md",
        "0003-tau-native-project-configuration.md",
        "0004-minimal-bundled-tool-boundary.md",
    }
