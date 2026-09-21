#!/usr/bin/env python3
"""Check the standalone board artifacts and their no-build asset budget."""

from __future__ import annotations

import argparse
import gzip
import re
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = "ms_tau_board/static/"
ASSETS = {
    "index.html",
    "app.js",
    "board.css",
    "bulma.min.css",
    "BULMA-LICENSE.txt",
}


def verify(dist_dir: Path) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    version = project["version"]
    wheel = dist_dir / f"ms_tau_board-{version}-py3-none-any.whl"
    sdist = dist_dir / f"ms_tau_board-{version}.tar.gz"
    if not wheel.is_file() or not sdist.is_file():
        raise ValueError("Board wheel and source distribution are both required")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        if any(name.startswith("ms_tau_sdk/") for name in names):
            raise ValueError("Board wheel contains Tau runtime code")
        missing = {STATIC + name for name in ASSETS} - names
        if missing:
            raise ValueError(f"Board wheel is missing assets: {sorted(missing)}")
        assets = [archive.read(STATIC + name) for name in ASSETS]
        if sum(map(len, assets)) >= 800 * 1024:
            raise ValueError("Board assets exceed the 800 KiB raw limit")
        if sum(len(gzip.compress(value)) for value in assets) >= 120 * 1024:
            raise ValueError("Board assets exceed the 120 KiB gzip limit")
        if len(archive.read(STATIC + "app.js")) >= 50 * 1024:
            raise ValueError("Board JavaScript exceeds the 50 KiB limit")
        html = archive.read(STATIC + "index.html").decode()
        if re.search(r"(?:src|href)=[\"']https?://", html):
            raise ValueError("Board HTML loads a remote asset")
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode())
        if metadata["Name"] != "ms-tau-board" or metadata["Version"] != version:
            raise ValueError("Board wheel identity does not match its project metadata")
        if metadata["Requires-Python"] != ">=3.13":
            raise ValueError("Board wheel has an unexpected Python requirement")
        dependency_names = {
            re.split(r"[<>=!\[; ]", requirement, maxsplit=1)[0].lower().replace("_", "-")
            for requirement in metadata.get_all("Requires-Dist", [])
        }
        if dependency_names != {"starlette", "uvicorn", "httpx"}:
            raise ValueError(f"Board wheel has unexpected dependencies: {sorted(dependency_names)}")
        entrypoint_name = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        if "tau-board = ms_tau_board.cli:main" not in archive.read(entrypoint_name).decode():
            raise ValueError("Board wheel has no tau-board command")
    with tarfile.open(sdist, "r:gz") as archive:
        names = {name.removeprefix(f"ms_tau_board-{version}/") for name in archive.getnames()}
        if {"src/" + STATIC + name for name in ASSETS} - names:
            raise ValueError("Board source distribution is missing assets")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist_dir", type=Path)
    arguments = parser.parse_args()
    verify(arguments.dist_dir)
    print("Verified ms-tau-board wheel, sdist, and asset budget")
