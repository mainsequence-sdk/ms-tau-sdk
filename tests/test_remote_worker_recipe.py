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
        assert "/opt/wheels/mainsequence_astro-*.whl" in recipe
        assert "mainsequence-astro==" not in recipe


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
    assert "USER ${NB_USER}" in recipe
    assert 'cd "${SKEL_APP_DIR}"' in recipe
    assert "git diff --quiet --ignore-submodules HEAD" in recipe
    assert "git diff --cached --quiet --ignore-submodules HEAD" in recipe
    assert "git branch --show-current" in recipe
    assert "git symbolic-ref HEAD" in recipe
    assert "git rev-parse HEAD" in recipe


def test_deployment_recipes_use_only_code_repository_runtime_names() -> None:
    runtime_recipe = (REPOSITORY_ROOT / "Dockerfile").read_text()
    worker_recipe = (REPOSITORY_ROOT / "Dockerfile.remote-worker").read_text()
    cloud_build = (REPOSITORY_ROOT / "deployment/gcp/cloudbuild.yaml").read_text()
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text()
    kubernetes = (
        REPOSITORY_ROOT / "deployment/kubernetes/astro-tau-deployment.yaml"
    ).read_text()
    active_contract = "\n".join(
        (runtime_recipe, worker_recipe, cloud_build, compose, kubernetes)
    )

    assert "ASTRO_CODE_REPOSITORY_CWD" in active_contract
    assert "ASTRO_FIXED_CODE_REPOSITORY_CWD" in worker_recipe
    assert "ASTRO_CODE_REPOSITORY_IMAGE_REF" in worker_recipe
    assert "code-repository-executor-bundle" in active_contract
    assert "pod-dockerfiles/code_repository_executor/Dockerfile" in cloud_build
    assert "ASTRO_PROJECT_" not in active_contract
    assert "project-executor" not in active_contract
    assert "project_executor" not in active_contract


def test_cloud_build_publishes_unrendered_provider_neutral_recipe() -> None:
    cloud_build = (REPOSITORY_ROOT / "deployment/gcp/cloudbuild.yaml").read_text()

    assert "export DOCKER_BUILDKIT=1" in cloud_build
    assert "gsutil cp Dockerfile.remote-worker" in cloud_build
    assert "Dockerfile.remote-worker.rendered" not in cloud_build
    assert 'sed "s|^ARG PROJECT_ID$' not in cloud_build
