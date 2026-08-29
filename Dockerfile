# syntax=docker/dockerfile:1.7

FROM python:3.13-slim AS astro-build

ARG ASTRO_RELEASE_VERSION=dev

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
 && rm -rf /var/lib/apt/lists/*

# Cache the hash-locked third-party wheelhouse independently from source edits.
COPY requirements-runtime.lock /tmp/requirements-runtime.lock
RUN --mount=type=cache,id=astro-pip-v2,target=/root/.cache/pip \
    python -m pip wheel \
    --require-hashes \
    --wheel-dir /opt/wheels \
    --requirement /tmp/requirements-runtime.lock

# Astro is a monorepo; both independently packageable tool distributions live
# under packages/ and are built from this repository's Docker context.
COPY packages/tau-file-tools /opt/src/tau-file-tools
COPY packages/tau-web-access /opt/src/tau-web-access
COPY . /app

RUN --mount=type=cache,id=astro-pip-v2,target=/root/.cache/pip \
    python -m pip wheel \
    --no-deps \
    --wheel-dir /opt/wheels \
    /opt/src/tau-file-tools \
    /opt/src/tau-web-access \
    /app \
 && python -m pip install \
    --no-index \
    --find-links /opt/wheels \
    /opt/wheels/mainsequence_astro-*.whl

FROM python:3.13-slim AS astro-runtime

ARG ASTRO_RELEASE_VERSION=dev

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ASTRO_RELEASE_VERSION=${ASTRO_RELEASE_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg \
    git \
    openssh-client \
    ripgrep \
 && rm -rf /var/lib/apt/lists/*

# Keep one system Python environment while excluding build-only OS packages.
COPY --from=astro-build /usr/local /usr/local

ENV APP_USER=jovyan \
    APP_GROUP=jovyan \
    APP_UID=10000 \
    APP_GID=10000 \
    HOME=/home/jovyan \
    ASTRO_HOME=/home/jovyan \
    ASTRO_HOST=0.0.0.0 \
    ASTRO_PORT=8787 \
    ASTRO_CODE_REPOSITORY_CWD=/workspace \
    ASTRO_A2A_ASSET_ROOT=/tmp/astro-a2a-assets \
    ASTRO_SESSION_ASSET_ROOT=/tmp/astro-session-assets

RUN groupadd --gid "${APP_GID}" "${APP_GROUP}" \
 && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home \
    --home-dir "${HOME}" --shell /bin/bash "${APP_USER}" \
 && mkdir -p /workspace "${ASTRO_A2A_ASSET_ROOT}" "${ASTRO_SESSION_ASSET_ROOT}" \
 && chown -R "${APP_UID}:${APP_GID}" "${HOME}" /workspace \
    "${ASTRO_A2A_ASSET_ROOT}" "${ASTRO_SESSION_ASSET_ROOT}"

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
