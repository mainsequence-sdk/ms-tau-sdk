import tomllib
from importlib.metadata import version
from pathlib import Path

from astro import __version__

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_package_metadata_is_the_runtime_version_source_of_truth() -> None:
    project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())
    project_version = project["project"]["version"]

    assert __version__ == project_version
    assert version("mainsequence-astro") == project_version


def test_container_recipes_install_the_locally_built_astro_wheel() -> None:
    for recipe_name in ("Dockerfile", "Dockerfile.remote-worker"):
        recipe = (REPOSITORY_ROOT / recipe_name).read_text()
        assert "mainsequence_astro-*.whl" in recipe
        assert "mainsequence-astro==" not in recipe


def test_main_image_implements_the_lean_python_runtime_abi() -> None:
    recipe = (REPOSITORY_ROOT / "Dockerfile").read_text()

    assert (
        "python:3.13-slim-bookworm@sha256:"
        "c45a22ea000adfd9cda29364bbe7edd23001ce5cc2ad15857cfbf7766943b9ca"
    ) in recipe
    assert "APP_USER=appuser" in recipe
    assert "APP_GROUP=appuser" in recipe
    assert "APP_UID=10000" in recipe
    assert "APP_GID=10000" in recipe
    assert "APP_HOME=/home/appuser" in recipe
    assert "HOME=/home/appuser" in recipe
    assert "VIRTUAL_ENV=/opt/venv" in recipe
    assert "PATH=/opt/venv/bin:" in recipe
    assert "ASTRO_CODE_REPOSITORY_CWD=/workspace" in recipe
    assert "ASTRO_SESSION_STATE_DIR=/session-state" in recipe
    assert "USER 10000:10000" in recipe
    assert "WORKDIR /workspace" in recipe
    assert "python -m pip check" in recipe
    assert "FROM python:3.13-slim AS" not in recipe
    assert "jovyan" not in recipe.lower()


def test_remote_worker_recipe_uses_backend_supplied_executor_bundle_image() -> None:
    recipe = (REPOSITORY_ROOT / "Dockerfile.remote-worker").read_text()

    first_from_offset = recipe.index("FROM ")
    assert "ARG EXECUTOR_BUNDLE_IMAGE" in recipe[:first_from_offset]
    assert "FROM ${EXECUTOR_BUNDLE_IMAGE} AS astro-executor-bundle" in recipe
    assert "europe-west1-docker.pkg.dev" not in recipe
    assert "ARG PROJECT_ID" not in recipe


def test_remote_worker_recipe_preserves_and_verifies_code_repository_git_context() -> None:
    recipe = (REPOSITORY_ROOT / "Dockerfile.remote-worker").read_text()

    assert "ARG SOURCE_COMMIT_SHA" in recipe
    assert "ARG SOURCE_REPOSITORY_BRANCH" in recipe
    assert "ARG SOURCE_REPOSITORY_REF" in recipe
    assert "USER 10000:10000" in recipe
    assert "WORKDIR /workspace" in recipe
    assert "test -d /workspace/.git" in recipe
    assert "git diff --quiet --ignore-submodules HEAD" in recipe
    assert "git diff --cached --quiet --ignore-submodules HEAD" in recipe
    assert "git branch --show-current" in recipe
    assert "git symbolic-ref HEAD" in recipe
    assert "git rev-parse HEAD" in recipe


def test_remote_worker_consumes_only_the_lean_python_runtime_abi() -> None:
    recipe = (REPOSITORY_ROOT / "Dockerfile.remote-worker").read_text()

    for retired_name in (
        "NB_USER",
        "NB_UID",
        "NB_GID",
        "SKEL_APP_DIR",
        "SKEL_DIR",
        "APP_DIR",
        "jovyan",
        "/opt/conda",
        "/usr/local/share/user-skel",
    ):
        assert retired_name not in recipe

    assert 'test "${APP_USER}" = "appuser"' in recipe
    assert 'test "${APP_HOME}" = "/home/appuser"' in recipe
    assert 'test "${VIRTUAL_ENV}" = "/opt/venv"' in recipe
    assert 'test "$(command -v python)" = "/opt/venv/bin/python"' in recipe
    assert "HOME=/home/appuser" in recipe
    assert "ASTRO_HOME=/home/appuser" in recipe
    assert "ASTRO_CODE_REPOSITORY_CWD=/workspace" in recipe
    assert "ASTRO_FIXED_CODE_REPOSITORY_CWD=/workspace" in recipe
    assert "ASTRO_CODE_REPOSITORY_EXTENSIONS_ENABLED=true" in recipe
    assert "PYTHONPATH=/workspace/src:/workspace" in recipe
    assert "ASTRO_SESSION_STATE_DIR=/session-state" in recipe
    assert "source=/opt/wheels,target=/opt/astro-wheels,ro" in recipe
    assert "COPY --chown=0:0 --from=astro-executor-bundle /app /app" in recipe
    assert "chown 0:0 /app" in recipe
    assert "COPY --from=astro-executor-bundle /opt/wheels" not in recipe
    assert "python -m pip check" in recipe
    assert "apt-get install -y --no-install-recommends ripgrep" in recipe
    assert "command -v rg" in recipe
    assert "ffmpeg" not in recipe
    assert "ffprobe" not in recipe
    assert "yt-dlp" not in recipe
    assert 'test "$(stat -c \'%u:%g\' /app)" = "0:0"' in recipe


def test_release_wheelhouse_excludes_optional_youtube_frame_downloader() -> None:
    web_project = tomllib.loads(
        (REPOSITORY_ROOT / "packages/tau-web-access/pyproject.toml").read_text()
    )
    runtime_dependencies = web_project["project"]["dependencies"]
    optional_dependencies = web_project["project"]["optional-dependencies"]
    runtime_lock = (REPOSITORY_ROOT / "requirements-runtime.lock").read_text()

    assert all(not dependency.startswith("yt-dlp") for dependency in runtime_dependencies)
    assert any(
        dependency.startswith("yt-dlp")
        for dependency in optional_dependencies["youtube-frames"]
    )
    assert "\nyt-dlp==" not in runtime_lock


def test_runtime_verification_does_not_require_media_tools() -> None:
    verification = (REPOSITORY_ROOT / "scripts/verify-runtime-image.sh").read_text()

    assert "command -v rg" in verification
    assert "command -v git" in verification
    assert "ffmpeg" not in verification
    assert "ffprobe" not in verification
    assert "yt-dlp" not in verification


def test_runtime_verification_exercises_project_extension_import_contract() -> None:
    verification = (REPOSITORY_ROOT / "scripts/verify-runtime-image.sh").read_text()
    fixture = REPOSITORY_ROOT / "tests/fixtures/project-extension-workspace"

    assert "ASTRO_CODE_REPOSITORY_EXTENSIONS_ENABLED" in verification
    assert "project-extension-workspace" in verification
    assert (fixture / "verify_extension.py").is_file()
    assert (fixture / ".tau/extensions/import_fixture/extension.py").is_file()
    assert (fixture / "src/project_fixture/service.py").is_file()


def test_active_deployment_examples_use_the_non_root_runtime_contract() -> None:
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text()
    kubernetes = (REPOSITORY_ROOT / "deployment/kubernetes/astro-tau-deployment.yaml").read_text()
    environment_example = (REPOSITORY_ROOT / ".env.example").read_text()
    active_contract = "\n".join((compose, kubernetes, environment_example))

    assert "/home/jovyan" not in active_contract
    assert "ASTRO_HOME: /home/appuser" in compose
    assert "ASTRO_HOME=/home/appuser" in environment_example
    assert "runAsNonRoot: true" in kubernetes
    assert "runAsUser: 10000" in kubernetes
    assert "runAsGroup: 10000" in kubernetes
    assert "allowPrivilegeEscalation: false" in kubernetes
    assert "type: RuntimeDefault" in kubernetes
    assert "- ALL" in kubernetes


def test_deployment_recipes_use_only_code_repository_runtime_names() -> None:
    runtime_recipe = (REPOSITORY_ROOT / "Dockerfile").read_text()
    worker_recipe = (REPOSITORY_ROOT / "Dockerfile.remote-worker").read_text()
    cloud_build = (REPOSITORY_ROOT / "deployment/gcp/cloudbuild.yaml").read_text()
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text()
    kubernetes = (REPOSITORY_ROOT / "deployment/kubernetes/astro-tau-deployment.yaml").read_text()
    active_contract = "\n".join((runtime_recipe, worker_recipe, cloud_build, compose, kubernetes))

    assert "ASTRO_CODE_REPOSITORY_CWD" in active_contract
    assert "ASTRO_FIXED_CODE_REPOSITORY_CWD" in worker_recipe
    assert "ASTRO_CODE_REPOSITORY_IMAGE_REF" in worker_recipe
    assert "code-repository-executor-bundle" in active_contract
    assert "pod-dockerfiles/code_repository_executor/Dockerfile" in cloud_build


def test_cloud_build_publishes_unrendered_provider_neutral_recipe() -> None:
    cloud_build = (REPOSITORY_ROOT / "deployment/gcp/cloudbuild.yaml").read_text()

    assert "export DOCKER_BUILDKIT=1" in cloud_build
    assert "gsutil cp Dockerfile.remote-worker" in cloud_build
    assert "Dockerfile.remote-worker.rendered" not in cloud_build
    assert 'sed "s|^ARG PROJECT_ID$' not in cloud_build
