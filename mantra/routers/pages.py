""" Static page routes for the UI server. """

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from mantra.services.health import _run_health_checks
import os

import logging

logger = logging.getLogger("mantra.pages")
router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC_DIR = os.path.join(BASE_DIR, "static")

@router.get("/", include_in_schema=False)
async def index():
    """Serve the login page."""
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))

@router.get("/dashboard", include_in_schema=False)
async def dashboard_page():
    """Serve the dashboard page."""
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))

@router.get("/console", include_in_schema=False)
async def console_page():
    """Serve the test console."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@router.get("/network", include_in_schema=False)
async def network_page():
    """Serve the network monitoring page."""
    return FileResponse(os.path.join(STATIC_DIR, "network.html"))

@router.get("/redis", include_in_schema=False)
async def redis_page():
    """Serve the Redis Monitoring & Inspector page."""
    return FileResponse(os.path.join(STATIC_DIR, "redis.html"))

@router.get("/kb-chat", include_in_schema=False)
async def kb_chat_page():
    """Serve the Knowledge Base text chat tester."""
    return FileResponse(os.path.join(STATIC_DIR, "kb_chat.html"))

@router.get("/health", tags=["System & Health"], summary="Service Health Check")
async def health():
    healthy = await _run_health_checks()
    return JSONResponse(
        content={"healthy": healthy}
    )

