#!/bin/bash
set -e

case "$1" in
  agent)
    echo "Starting LiveKit Agent..."
    exec uv run python -m mantra.agent start
    ;;
  ui)
    echo "Starting UI Server (FastAPI)..."
    exec uv run python -m mantra.ui_server
    ;;
  dispatcher)
    echo "Starting Background Dispatcher..."
    exec uv run python -m mantra.dispatcher
    ;;
  mcp)
    echo "Starting MCP Database Server..."
    exec uv run python mcp/server.py
    ;;
  all)
    echo "Starting Mantra Voice Stack (UI Server + Dispatcher + Agent)..."
    uv run python -m mantra.dispatcher &
    uv run python -m mantra.agent start &
    exec uv run python -m mantra.ui_server
    ;;
  *)
    echo "Usage: $0 {agent|ui|dispatcher|mcp|all}"
    echo "Defaulting to all (combined mode)..."
    uv run python -m mantra.dispatcher &
    uv run python -m mantra.agent start &
    exec uv run python -m mantra.ui_server
    ;;
esac
