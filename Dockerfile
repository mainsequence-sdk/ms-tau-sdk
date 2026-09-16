# syntax=docker/dockerfile:1.7

ARG PYTHON_BASE_IMAGE=python:3.13-slim-bookworm@sha256:c45a22ea000adfd9cda29364bbe7edd23001ce5cc2ad15857cfbf7766943b9ca

FROM ${PYTHON_BASE_IMAGE} AS astro-build

ARG ASTRO_RELEASE_VERSION=dev

ENV VIRTUAL_ENV=/opt/build-venv \
    PATH=/opt/build-venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
 && rm -rf /var/lib/apt/lists/* \
 && python -m venv "${VIRTUAL_ENV}"

# Cache the hash-locked third-party wheelhouse independently from source edits.
COPY requirements-runtime.lock /tmp/requirements-runtime.lock
RUN --mount=type=cache,id=astro-pip-v2,target=/root/.cache/pip \
    python -m pip wheel \
    --require-hashes \
    --wheel-dir /opt/wheels \
    --requirement /tmp/requirements-runtime.lock

COPY . /app

RUN --mount=type=cache,id=astro-pip-v2,target=/root/.cache/pip \
    python -m pip wheel \
    --no-deps \
    --wheel-dir /opt/wheels \
    /app \
 && python -m pip install \
    --no-index \
    --find-links /opt/wheels \
    /opt/wheels/mainsequence_astro-*.whl \
 && python -m pip check \
 && python -c "import astro, tau_agent, tau_coding"

FROM ${PYTHON_BASE_IMAGE} AS astro-runtime

ARG ASTRO_RELEASE_VERSION=dev

ENV APP_USER=appuser \
    APP_GROUP=appuser \
    APP_UID=10000 \
    APP_GID=10000 \
    APP_HOME=/home/appuser \
    HOME=/home/appuser \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ASTRO_RELEASE_VERSION=${ASTRO_RELEASE_VERSION} \
    ASTRO_HOME=/home/appuser \
    ASTRO_CONTAINER_DATA_DIR=/home/appuser/.astro-container-data \
    ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/appuser/.astro-container-data/.config/mainsequence \
    PI_CODING_AGENT_DIR=/home/appuser/.astro-container-data/.pi/agent \
    ASTRO_HOST=0.0.0.0 \
    ASTRO_PORT=8787 \
    ASTRO_CODE_REPOSITORY_CWD=/workspace \
    ASTRO_SESSION_STATE_DIR=/session-state \
    ASTRO_STREAM_SESSION_DIR=/session-state/sessions \
    ASTRO_SESSION_OVERRIDES_DIR=/session-state/session-overrides \
    ASTRO_PROVIDER_CREDENTIAL_DIR=/session-state/pi-agent-auth \
    ASTRO_A2A_ASSET_ROOT=/tmp/astro-a2a-assets

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    openssh-client \
    ripgrep \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${APP_GID}" "${APP_GROUP}" \
 && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home \
    --home-dir "${HOME}" --shell /bin/bash "${APP_USER}" \
 && python -m venv "${VIRTUAL_ENV}" \
 && mkdir -p \
    /workspace \
    "${ASTRO_CONTAINER_DATA_DIR}" \
    "${ASTRO_MAINSEQUENCE_CONFIG_DIR}" \
    "${PI_CODING_AGENT_DIR}" \
    "${ASTRO_STREAM_SESSION_DIR}" \
    "${ASTRO_SESSION_OVERRIDES_DIR}" \
    "${ASTRO_PROVIDER_CREDENTIAL_DIR}" \
    "${ASTRO_A2A_ASSET_ROOT}" \
 && chown -R "${APP_UID}:${APP_GID}" \
    "${HOME}" \
    /workspace \
    /session-state \
    "${ASTRO_A2A_ASSET_ROOT}"

# Install only the builder-produced wheelhouse into the one runtime venv. The
# wheelhouse is mounted from the build stage and is not retained in a layer.
RUN --mount=from=astro-build,source=/opt/wheels,target=/opt/wheels,ro \
    python -m pip install \
      --no-index \
      --find-links /opt/wheels \
      /opt/wheels/mainsequence_astro-*.whl \
 && python -m pip check \
 && python -c "import astro, tau_agent, tau_coding"

USER 10000:10000
WORKDIR /workspace

EXPOSE 8787

CMD ["astro-stream"]

FROM scratch AS code-repository-executor-bundle

ARG ASTRO_RELEASE_VERSION=dev

LABEL org.opencontainers.image.title="astro-${ASTRO_RELEASE_VERSION}" \
    org.opencontainers.image.description="Python 3.13 Tau code repository executor source bundle." \
    org.opencontainers.image.version="${ASTRO_RELEASE_VERSION}"

COPY . /app
COPY --from=astro-build /opt/wheels /opt/wheels

FROM astro-runtime AS astro
