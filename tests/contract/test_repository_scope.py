from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEXT_ROOTS = (".github", "docs", "scripts", "src", "tests")
FORBIDDEN_PUBLICATION_MARKERS = (
    "-docker.pkg.dev",
    "cloudbuild.yaml",
    "deployment/gcp",
    "docker push",
    "gcr.io/cloud-builders",
    "gcloud builds submit",
)


def test_repository_has_no_provider_specific_image_publication_surface() -> None:
    assert not (ROOT / "deployment").exists()

    violations: list[str] = []
    for root_name in TEXT_ROOTS:
        for path in (ROOT / root_name).rglob("*"):
            if not path.is_file() or path.suffix in {".pyc", ".pyo"}:
                continue
            if path == Path(__file__):
                continue
            try:
                text = path.read_text(encoding="utf-8").lower()
            except UnicodeDecodeError:
                continue
            for marker in FORBIDDEN_PUBLICATION_MARKERS:
                if marker in text:
                    violations.append(f"{path.relative_to(ROOT)}: {marker}")

    assert violations == []


def test_sdk_does_not_depend_on_or_import_mainsequence_python_package() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    normalized = [
        re.split(r"[<>=!~ ;\[]", dependency.lower().replace("_", "-"), maxsplit=1)[0]
        for dependency in dependencies
    ]

    assert not any(dependency == "mainsequence" for dependency in normalized)

    imports: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped == "import mainsequence" or stripped.startswith("from mainsequence "):
                imports.append(f"{path.relative_to(ROOT)}:{line_number}")

    assert imports == []
