FROM python:3.11-slim AS astro-base

ARG ASTRO_RELEASE_VERSION=dev

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/node_modules/.bin:${PATH}" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    ASTRO_RELEASE_VERSION=${ASTRO_RELEASE_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg \
    openssh-client \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json package-lock.json tsconfig.json ./
RUN npm install --include=dev --no-audit --no-fund \
 && npm cache clean --force

COPY .pi ./.pi
COPY pi ./pi
COPY tmp_ms_pi ./tmp_ms_pi
COPY interface ./interface
COPY runtime ./runtime
COPY adapters ./adapters
COPY bin ./bin
COPY tools ./tools

RUN node tools/build/patch-pi-rpc-ready.mjs \
 && npm run check

FROM astro-base AS astro-mainsequence

ARG MAINSEQUENCE_PIP_SPEC=mainsequence
ENV MAINSEQUENCE_PIP_SPEC=${MAINSEQUENCE_PIP_SPEC}

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "${MAINSEQUENCE_PIP_SPEC}" uv playwright \
 && playwright install --with-deps chromium

FROM astro-mainsequence AS astro-runtime

ENV APP_USER=jovyan \
    APP_GROUP=jovyan \
    APP_UID=10000 \
    APP_GID=10000 \
    APP_HOME=/home/jovyan \
    HOME=/home/jovyan \
    ASTRO_CONTAINER_DATA_DIR=/home/jovyan/.astro-container-data \
    ASTRO_STREAM_SESSION_DIR=/session-state/sessions \
    ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/jovyan/.astro-container-data/.config/mainsequence \
    PI_CODING_AGENT_DIR=/home/jovyan/.astro-container-data/.pi/agent

RUN groupadd --gid "${APP_GID}" "${APP_GROUP}" \
 && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --home-dir "${APP_HOME}" --shell /bin/bash "${APP_USER}" \
 && mkdir -p \
    "${APP_HOME}/.astro-container-data/.pi/agent/bin" \
    "${APP_HOME}/.astro-container-data/.config/mainsequence" \
    "/session-state/sessions" \
    "/session-state/session-overrides" \
    "/ms-playwright" \
    "${APP_HOME}/.local/share" \
    "${APP_HOME}/.pi" \
    "${APP_HOME}/.config" \
    "${APP_HOME}/.astro" \
 && chown -R "${APP_USER}:${APP_GROUP}" "${APP_HOME}" /session-state /ms-playwright

USER jovyan

FROM scratch AS project-executor-bundle

COPY --from=astro-base /app /app

FROM astro-runtime AS astro-pi-stream

ENV ASTRO_STREAM_HOST=0.0.0.0 \
    ASTRO_STREAM_PORT=8787

EXPOSE 8787

CMD ["/app/node_modules/.bin/tsx", "bin/astro-stream.ts"]

FROM astro-runtime AS astro-session-checkpoint-sidecar

CMD ["/app/node_modules/.bin/tsx", "runtime/checkpoints/sidecar.ts"]

FROM astro-runtime AS astro-pi

CMD ["/app/node_modules/.bin/tsx", "bin/astro-pi-local.ts"]
