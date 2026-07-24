#!/bin/bash

# Function to handle cleanup on exit
cleanup() {
    echo ""
    echo "Stopping services..."
    kill $MCP_PID $AGENT_PID $UI_PID 2>/dev/null
    exit
}

# Trap SIGINT (Ctrl+C) and SIGTERM
trap cleanup SIGINT SIGTERM

echo "Starting MCP Database Server..."
uv run python mcp/server.py &
MCP_PID=$!

echo "Starting LiveKit Agent (dev mode)..."
uv run python -m mantra.agent dev &
AGENT_PID=$!

echo "Starting UI Server (FastAPI)..."
uv run python -m mantra.ui_server &
UI_PID=$!

# Get local IP address (works on Linux/macOS)
LOCAL_IP=$(hostname -I | awk '{print $1}')
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP="localhost"
fi

echo ""
echo "----------------------------------------------------------------"
echo " Everything is running!"
echo " Webhook URL:  http://$LOCAL_IP:8081/api/v1/webhooks/telephony"
echo " SIP (Twilio): http://$LOCAL_IP:8081/api/v1/sip/trunks/outbound/twilio"
echo " SIP (Zadarma): http://$LOCAL_IP:8081/api/v1/sip/trunks/outbound/zadarma"
echo " SIP (Plivo):   http://$LOCAL_IP:8081/api/v1/sip/trunks/outbound/plivo"
echo " List Trunks:   GET http://$LOCAL_IP:8081/api/v1/sip/trunks/outbound"
echo " Delete Trunk:  DELETE http://$LOCAL_IP:8081/api/v1/sip/trunks/outbound/{trunk_id}"
echo " MCP Server:    MCP protocol on stdio"
echo " Inbound Trunk CRUD: http://$LOCAL_IP:8081/api/v1/sip/trunks/inbound"
echo " Dispatch Rule CRUD: http://$LOCAL_IP:8081/api/v1/sip/dispatch-rules"
echo "----------------------------------------------------------------"
echo "To trigger a call, send a POST to the Webhook URL."
echo "To setup SIP, send a POST to the corresponding SIP URL."
echo "To manage trunks, use the List and Delete endpoints above."
echo "----------------------------------------------------------------"
echo ""
echo "Press Ctrl+C to stop all services."

# Wait for background processes to finish
wait $MCP_PID $AGENT_PID $UI_PID
