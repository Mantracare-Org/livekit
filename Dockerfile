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
RUN uv sync --locked --no-install-project --no-dev

COPY . .
RUN uv sync --locked --no-dev

# --- Production stage ---
FROM base

ENV UV_COMPILE_BYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"
# Set model cache directories to be inside /app
ENV HF_HOME=/app/.cache/huggingface
ENV HF_HUB_CACHE=/app/.cache/huggingface
ENV TORCH_HOME=/app/.cache/torch
ENV SILERO_VAD_CACHE=/app/.cache/silero

ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/app" \
    --shell "/sbin/nologin" \
    --uid "${UID}" \
    appuser

# Install system dependencies required by LiveKit plugins (silero, onnxruntime, etc.)
RUN apt-get update && apt-get install -y \
    libgomp1 \
    libglib2.0-0 \
    libasound2 \
    libatomic1 \
    libportaudio2 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy the application and virtualenv from the build stage
COPY --from=build --chown=appuser:appuser /app /app
WORKDIR /app

# Ensure cache directory exists and is writable by appuser
RUN mkdir -p /app/.cache && chown -R appuser:appuser /app/.cache

USER appuser

# Download required models so they are cached in the image
# We run this as appuser so the cache is correctly owned and located
RUN uv run python -m mantra.agent download-files

# Copy and setup entrypoint
COPY --chown=appuser:appuser entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Expose the UI Server port
EXPOSE 8081

# Use entrypoint to switch between agent and ui
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["agent"]
