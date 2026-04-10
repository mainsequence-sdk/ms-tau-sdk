FROM python:3.11-slim AS astro-base

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/node_modules/.bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg \
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
COPY interface ./interface
COPY scripts ./scripts

RUN npm run check

FROM astro-base AS astro-mainsequence

ARG MAINSEQUENCE_PIP_SPEC=mainsequence
ENV MAINSEQUENCE_PIP_SPEC=${MAINSEQUENCE_PIP_SPEC}

RUN pip install --no-cache-dir "${MAINSEQUENCE_PIP_SPEC}"

FROM astro-mainsequence AS astro-pi-stream

ENV ASTRO_STREAM_HOST=0.0.0.0 \
    ASTRO_STREAM_PORT=8787

EXPOSE 8787

CMD ["tsx", "scripts/start_pi_stream.ts"]

FROM astro-mainsequence AS astro-pi

CMD ["node", "scripts/start_pi.mjs"]
