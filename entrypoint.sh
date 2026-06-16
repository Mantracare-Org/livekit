#!/bin/bash
set -e

case "$1" in
  agent)
    echo "Starting LiveKit Agent..."
    exec uv run --no-project python -m mantra.agent start
    ;;
  ui)
    echo "Starting UI Server (FastAPI)..."
    exec uv run --no-project python -m mantra.ui_server
    ;;
  *)
    echo "Usage: $0 {agent|ui}"
    echo "Defaulting to agent..."
    exec uv run --no-project python -m mantra.agent start
    ;;
esac
