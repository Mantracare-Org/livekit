"""Kettle UI Server — FastAPI application assembly.

Route handlers live in ``mantra.routers.*``; the heavy client/capacity/health
logic lives in ``mantra.services.*``. This module owns the FastAPI app, its
lifespan, middleware, and static mounting.
"""
import logging
import os
import sys
import time
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from colorama import init as colorama_init
from dotenv import load_dotenv
from livekit import api

colorama_init(autoreset=True)


# Load environment variables from .env.local
load_dotenv(".env.local")
load_dotenv(".env.self", override=True)  # Self-host override (if present)

from mantra.email_alerts import send_crash_email
from mantra.services import clients as _svc_clients
from mantra.services.clients import close_clients, init_clients
from mantra.services.health import _run_health_checks, health_gate_middleware
from mantra.services.webhook_queue import process_pending_webhooks
from mantra.routers import (
    auth,
    dashboard,
    knowledge,
    org_configs,
    pages,
    redis_api,
    sip,
    telephony,
)

from mantra.routers.pages import STATIC_DIR


logger = logging.getLogger("mantra.ui_server")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s INFO %(name)s: %(message)s"))
if not logger.handlers:
    logger.addHandler(_handler)
logger.propagate = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_clients()

    # ── Startup healthcheck ─────────────────────────────────────────
    logger.info("Running startup healthcheck on all dependencies...")
    if await _run_health_checks():
        logger.info("Startup healthcheck: ALL SERVICES HEALTHY")
    else:
        logger.warning("Startup healthcheck: one or more services down — refusing dispatch")
    # ────────────────────────────────────────────────────────────────

    # ── Startup zombie-room cleanup ─────────────────────────────────
    if _svc_clients.lk_client:
        try:
            response = await _svc_clients.lk_client.room.list_rooms(api.ListRoomsRequest())
            zombie_count = 0
            for room in response.rooms:
                if not room.name or not room.name.startswith("call_"):
                    continue
                if room.num_participants == 0:
                    logger.warning(
                        f"Startup zombie room detected: {room.name} (0 participants). Deleting."
                    )
                    try:
                        await _svc_clients.lk_client.room.delete_room(
                            api.DeleteRoomRequest(room=room.name)
                        )
                        zombie_count += 1
                    except Exception as e:
                        logger.error(f"Failed to delete zombie room {room.name}: {e}")
            if zombie_count > 0:
                logger.info(f"Startup zombie cleanup: deleted {zombie_count} empty rooms")
        except Exception as e:
            logger.error(f"Startup zombie room cleanup failed: {e}")
    # ────────────────────────────────────────────────────────────────

    # Start background webhook worker
    webhook_task = asyncio.create_task(process_pending_webhooks())

    yield

    webhook_task.cancel()
    try:
        await webhook_task
    except asyncio.CancelledError:
        pass

    await close_clients()


app = FastAPI(lifespan=lifespan)
Instrumentator().instrument(app).expose(app, include_in_schema=False, should_gzip=True)

SCANNER_PATHS = (
    "/.well-known/",
    "/favicon",
    "/wp-",
    "/blog/",
    "/web/",
    "/wordpress/",
    "/website/",
    "/wp/",
    "/news/",
    "/2018/",
    "/2019/",
    "/shop/",
    "/wp1/",
    "/test/",
    "/media/",
    "/wp2/",
    "/site/",
    "/cms/",
    "/sito/",
)


@app.exception_handler(Exception)
async def global_crash_exception_handler(request: Request, exc: Exception):
    logger.error(f"Error in UI server: {exc}", exc_info=True)

    context_data = {
        "Request URL": str(request.url),
        "HTTP Method": request.method,
        "User-Agent": request.headers.get("User-Agent"),
        "Client IP": request.client.host if request.client else None,
    }

    await send_crash_email(
        service_name="Mantra UI Server", error=exc, context_data=context_data
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error, An Automated alert has been dispatched. The technical team is working on resolving this issue."
        },
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    client_host = request.client.host if request.client else "unknown"
    path = request.url.path
    try:
        response = await call_next(request)
        duration = time.time() - start

        # Keep high-frequency read-only monitor polling out of normal logs.
        if path.startswith("/v1/redis/") and request.method == "GET":
            logger.debug(
                f"{client_host} {request.method} {path} {response.status_code} in {duration * 1000:.0f}ms"
            )
        # Suppress scanner junk at INFO level
        elif path.startswith(SCANNER_PATHS):
            logger.debug(
                f"Scanner: {client_host} {request.method} {path} {response.status_code}"
            )
        else:
            logger.info(
                f"{client_host} {request.method} {path} {response.status_code} in {duration * 1000:.0f}ms"
            )

        return response
    except Exception as e:
        duration = time.time() - start
        logger.error(
            f"{client_host} {request.method} {path} ERROR in {duration * 1000:.0f}ms: {e}"
        )
        raise  # let FastAPI handle the error response


@app.middleware("http")
async def health_gate_wrapper(request: Request, call_next):
    return await health_gate_middleware(request, call_next)


# Mount static files (with HTML files as default)
app.mount("/static", StaticFiles(directory=STATIC_DIR, html=True), name="static")

# ── Routers ────────────────────────────────────────────────────────────
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(knowledge.router)
app.include_router(sip.router)
app.include_router(telephony.router)
app.include_router(dashboard.router)
app.include_router(redis_api.router)
app.include_router(org_configs.router)


def main():
    import uvicorn

    port = int(os.getenv("PORT", "8081"))
    logger.info(f"UI Server starting on http://0.0.0.0:{port}")
    try:
        uvicorn.run("mantra.ui_server:app", host="0.0.0.0", port=port, access_log=False)
    except Exception as e:
        logger.error(f"Failed to run UI server: {e}", exc_info=True)
        try:
            asyncio.run(
                send_crash_email(
                    service_name="Mantra UI Server (Core/Startup)",
                    error=e,
                    context_data={
                        "Status": "Crashloop / Process Death",
                        "PID": os.getpid(),
                    },
                )
            )
        except Exception as email_err:
            logger.error(f"Failed to dispatch core crash email: {email_err}")
        raise


if __name__ == "__main__":
    main()