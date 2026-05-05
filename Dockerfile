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

ENV UV_COMPILE_BYTECODE=1
ENV UV_SYSTEM_PYTHON=1

ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/app" \
    --shell "/sbin/nologin" \
    --uid "${UID}" \
    appuser

# Copy the application from the build stage
COPY --from=build --chown=appuser:appuser /app /app
WORKDIR /app

# Download required models so they are cached in the image
# We do this as root to avoid permission issues with the cache dir, 
# then we ensure everything is owned by appuser.
RUN uv run python -m mantra.agent download-files && \
    chown -R appuser:appuser /app

# Copy and setup entrypoint
COPY --chown=appuser:appuser entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

USER appuser

# Use entrypoint to switch between agent and ui
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["agent"]
