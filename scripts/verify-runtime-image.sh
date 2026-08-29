#!/usr/bin/env bash
set -euo pipefail

image="${1:-astro:tau}"
container_name="astro-tau-verify-$$"

cleanup() {
  docker rm -f "${container_name}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --rm --entrypoint python "${image}" -c '
import importlib.util
import pathlib
import subprocess
import sys

assert sys.version_info >= (3, 13), sys.version
prefix = pathlib.Path(sys.prefix).resolve()
assert prefix == pathlib.Path("/opt/venv"), prefix
for module_name in (
    "astro",
    "tau_agent",
    "tau_coding",
    "tau_file_tools",
    "tau_web_access",
    "mcp",
    "structlog",
):
    spec = importlib.util.find_spec(module_name)
    assert spec is not None and spec.origin is not None, module_name
    pathlib.Path(spec.origin).resolve().relative_to(prefix)

child_executable = subprocess.check_output(
    [sys.executable, "-c", "import sys; print(sys.executable)"],
    text=True,
).strip()
assert child_executable == sys.executable, (child_executable, sys.executable)
print(sys.version)
print(sys.executable)
'

docker run --rm --entrypoint sh "${image}" -ceu '
test "$(id -u)" = "10000"
test "$(id -g)" = "10000"
test "${APP_HOME}" = "/home/appuser"
test "${HOME}" = "/home/appuser"
test "${VIRTUAL_ENV}" = "/opt/venv"
test "$(command -v python)" = "/opt/venv/bin/python"
test "$(pwd)" = "/workspace"
test -d /home/appuser
test ! -e /home/jovyan
if command -v node >/dev/null 2>&1; then
  echo "Node must not be installed in the Tau runtime image" >&2
  exit 1
fi
command -v ffmpeg
command -v ffprobe
command -v git
command -v rg
command -v yt-dlp
'

docker run --detach \
  --name "${container_name}" \
  --env MAINSEQUENCE_BACKEND=http://127.0.0.1:8000 \
  --env MAINSEQUENCE_RUNTIME_CREDENTIAL_ID=container-verification \
  --env MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET=container-verification \
  --env ASTRO_STARTUP_DEPENDENCIES_ENABLED=false \
  "${image}" >/dev/null

probe='
import json
import urllib.request

for path in ("/health", "/ready"):
    with urllib.request.urlopen(f"http://127.0.0.1:8787{path}", timeout=2) as response:
        payload = json.load(response)
    assert payload["ok"] is True
    assert payload["runtime"] == "tau"
'

for _attempt in $(seq 1 30); do
  if docker exec "${container_name}" python -c "${probe}" >/dev/null 2>&1; then
    echo "Runtime image verification passed: ${image}"
    exit 0
  fi
  sleep 1
done

docker logs "${container_name}" >&2
echo "Astro health/readiness verification timed out" >&2
exit 1
