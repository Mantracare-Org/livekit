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

echo "Starting UI Server (FastAPI)..."
uv run uvicorn ui_server:app --host 0.0.0.0 --port 5000 &
UI_PID=$!

# Get local IP address (works on Linux/macOS)
LOCAL_IP=$(hostname -I | awk '{print $1}')
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP="localhost"
fi

echo ""
echo "----------------------------------------------------------------"
echo " Everything is running!"
echo " Webhook URL: http://$LOCAL_IP:5000/webhook/<event_name>"
echo "To trigger a call from another system, send a POST request to this URL. For <event_name> use anything"
echo "----------------------------------------------------------------"
echo ""
echo "Press Ctrl+C to stop both."

# Wait for background processes to finish
wait $AGENT_PID $UI_PID
