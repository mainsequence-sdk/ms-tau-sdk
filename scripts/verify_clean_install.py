#!/usr/bin/env python3
"""Install an SDK wheel outside the checkout and verify its public process surface."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _venv_executable(venv: Path, name: str) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv / directory / f"{name}{suffix}"


def _free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _wait_for_json(
    url: str, process: subprocess.Popen[str], timeout: float = 30
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout = process.stdout.read() if process.stdout is not None else ""
            raise RuntimeError(f"ms-tau exited before becoming healthy:\n{stdout}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    if not wheel.is_file():
        raise SystemExit(f"wheel does not exist: {wheel}")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required for clean-install verification")

    with tempfile.TemporaryDirectory(prefix="ms-tau-sdk-clean-install-") as temporary:
        root = Path(temporary)
        venv = root / "venv"
        workspace = root / "project"
        workspace.mkdir()
        subprocess.run([uv, "venv", "--python", "3.13", str(venv)], check=True)
        python = _venv_executable(venv, "python")
        subprocess.run([uv, "pip", "install", "--python", str(python), str(wheel)], check=True)

        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.update(
            {
                "MAINSEQUENCE_TAU_HOST": "127.0.0.1",
                "MAINSEQUENCE_TAU_PORT": str(_free_port()),
                "MAINSEQUENCE_TAU_STARTUP_DEPENDENCIES_ENABLED": "false",
                "MAINSEQUENCE_RUNTIME_CREDENTIAL_ID": "offline-release-verification",
                "MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET": "offline-release-verification",
                "PYTHONNOUSERSITE": "1",
            }
        )
        import_check = (
            "import importlib.util, pathlib, sys; "
            "import ms_tau_sdk; "
            "from ms_tau_sdk import TauSDKSettings, create_app; "
            "path = pathlib.Path(ms_tau_sdk.__file__).resolve(); "
            "assert pathlib.Path(sys.prefix).resolve() in path.parents, path; "
            "assert importlib.util.find_spec('astro') is None; "
            "assert importlib.util.find_spec('mainsequence') is None; "
            "settings = TauSDKSettings(workspace=pathlib.Path.cwd()); "
            "assert create_app(settings).title == 'Main Sequence TAU SDK'; "
            "local = TauSDKSettings("
            "workspace=pathlib.Path.cwd(), auth_mode='jwt', local_mode=True, "
            "access_token='verification-access', refresh_token='verification-refresh', "
            "local_provider='openai', local_model='verification-model'); "
            "assert local.host == '127.0.0.1'; "
            "assert create_app(local).state.settings.local_mode is True"
        )
        subprocess.run(
            [str(python), "-P", "-c", import_check],
            cwd=workspace,
            env=environment,
            check=True,
        )

        command = _venv_executable(venv, "ms-tau")
        subprocess.run(
            [str(command), "skills", "sync", "--path", str(workspace), "--json"],
            cwd=workspace,
            env=environment,
            check=True,
        )
        managed_skills = workspace / ".agents" / "skills" / "ms_tau_sdk"
        expected_skills = {
            "tau_a2a_runtime_adapter",
            "tau_local_development",
            "tau_project_customization",
            "tau_repository_integration",
        }
        installed_skills = {path.parent.name for path in managed_skills.glob("*/SKILL.md")}
        if installed_skills != expected_skills:
            raise RuntimeError(f"unexpected installed skill bundle: {installed_skills}")
        pin = (managed_skills / "PINNED_FROM.txt").read_text(encoding="utf-8")
        if "library_name=ms-tau-sdk" not in pin:
            raise RuntimeError("installed skill bundle has no ms-tau-sdk provenance")

        process = subprocess.Popen(
            [str(command)],
            cwd=workspace,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        port = environment["MAINSEQUENCE_TAU_PORT"]
        try:
            health = _wait_for_json(f"http://127.0.0.1:{port}/health", process)
            version = _wait_for_json(f"http://127.0.0.1:{port}/version", process)
            if health.get("ok") is not True or health.get("runtime") != "tau":
                raise RuntimeError(f"unexpected health response: {health}")
            if version.get("runtime") != "tau" or not version.get("version"):
                raise RuntimeError(f"unexpected version response: {version}")
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            output = (
                process.stdout.read()
                if process.stdout is not None and not process.stdout.closed
                else ""
            )
        if process.returncode != 0:
            raise RuntimeError(
                f"ms-tau did not shut down cleanly ({process.returncode}):\n{output}"
            )
        print(f"clean install verified: {version['version']} from {wheel.name}")


if __name__ == "__main__":
    main()
