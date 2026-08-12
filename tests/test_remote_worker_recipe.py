from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_remote_worker_recipe_uses_backend_supplied_executor_bundle_image() -> None:
    recipe = (REPOSITORY_ROOT / "Dockerfile.remote-worker").read_text()

    first_from_offset = recipe.index("FROM ")
    assert "ARG EXECUTOR_BUNDLE_IMAGE" in recipe[:first_from_offset]
    assert "FROM ${EXECUTOR_BUNDLE_IMAGE} AS astro-executor-bundle" in recipe
    assert "europe-west1-docker.pkg.dev" not in recipe
    assert "ARG PROJECT_ID" not in recipe


def test_cloud_build_publishes_unrendered_provider_neutral_recipe() -> None:
    cloud_build = (REPOSITORY_ROOT / "deployment/gcp/cloudbuild.yaml").read_text()

    assert "gsutil cp Dockerfile.remote-worker" in cloud_build
    assert "Dockerfile.remote-worker.rendered" not in cloud_build
    assert 'sed "s|^ARG PROJECT_ID$' not in cloud_build
