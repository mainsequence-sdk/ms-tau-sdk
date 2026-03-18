FROM python:3.11-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv \
 && uv pip install --system mainsequence

RUN mkdir -p /Users /workspace/astro

WORKDIR /workspace/astro
