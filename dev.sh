#!/bin/bash

# Function to handle cleanup on exit
cleanup() {
    echo ""
    echo "Stopping agent and UI server..."
    kill $AGENT_PID $UI_PID 2>/dev/null
    exit
}

# Trap SIGINT (Ctrl+C) and SIGTERM
trap cleanup SIGINT SIGTERM

echo "Starting LiveKit Agent (dev mode)..."
uv run python agent.py dev &
AGENT_PID=$!

echo "Starting UI Server..."
uv run python ui_server.py &
UI_PID=$!

echo "Both processes are running. Press Ctrl+C to stop both."

# Wait for background processes to finish
wait $AGENT_PID $UI_PID
