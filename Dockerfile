# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS base

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# --- Build stage ---
FROM base AS build

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    python3-dev \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --locked

COPY . .

# --- Production stage ---
FROM base

ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/app" \
    --shell "/sbin/nologin" \
    --uid "${UID}" \
    appuser

COPY --from=build --chown=appuser:appuser /app /app
WORKDIR /app
USER appuser

# Download required models so they are cached in the image
RUN uv run python -m mantra.agent download-files

# Run the Agent by default
CMD ["uv", "run", "python", "-m", "mantra.agent", "start"]
