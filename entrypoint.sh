#!/bin/bash
set -e

case "$1" in
  agent)
    echo "Starting LiveKit Agent..."
    exec python -m mantra.agent start
    ;;
  ui)
    echo "Starting UI Server (FastAPI)..."
    exec python -m mantra.ui_server
    ;;
  *)
    echo "Usage: $0 {agent|ui}"
    echo "Defaulting to agent..."
    exec python -m mantra.agent start
    ;;
esac
