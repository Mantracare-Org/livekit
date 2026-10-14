""" Static page routes for the UI server. """

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from mantra.services.health import _run_health_check_details, _run_health_checks
import os

import logging

logger = logging.getLogger("mantra.pages")
router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC_DIR = os.path.join(BASE_DIR, "static")

@router.get("/")
async def index():
    """Serve the login page."""
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))

@router.get("/dashboard")
async def dashboard_page():
    """Serve the dashboard page."""
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))

@router.get("/console")
async def console_page():
    """Serve the test console."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@router.get("/network")
async def network_page():
    """Serve the network monitoring page."""
    return FileResponse(os.path.join(STATIC_DIR, "network.html"))

@router.get("/redis")
async def redis_page():
    """Serve the Redis Monitoring & Inspector page."""
    return FileResponse(os.path.join(STATIC_DIR, "redis.html"))

@router.get("/kb-chat")
async def kb_chat_page():
    """Serve the Knowledge Base text chat tester."""
    return FileResponse(os.path.join(STATIC_DIR, "kb_chat.html"))

@router.get("/health")
async def health():
    healthy = await _run_health_checks()
    return JSONResponse(
        content={"healthy": healthy}
    )

@router.get("/health/details")
async def health_details():
    """Return service health details for the dashboard status popover."""
    healthy, checks = await _run_health_check_details()
    return JSONResponse(
        content={
            "healthy": healthy,
            "checks": {
                service: status if status is True else False
                for service, status in checks.items()
            },
        }
    )

