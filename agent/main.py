"""Flow Kit — FastAPI + WebSocket server entry point."""
import asyncio
import hmac
import json
import logging
import signal
from contextlib import asynccontextmanager

import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from agent.config import API_HOST, API_PORT, WS_HOST, WS_PORT, BACKUP_INTERVAL_SECONDS
from agent.db.schema import init_db, close_db
from agent.api.characters import router as characters_router
from agent.api.projects import router as projects_router
from agent.api.videos import router as videos_router
from agent.api.scenes import router as scenes_router
from agent.api.requests import router as requests_router
from agent.api.flow import router as flow_router
from agent.api.reviews import router as reviews_router
from agent.api.tts import router as tts_router
from agent.api.materials import router as materials_router
from agent.api.music import router as music_router
from agent.api.models import router as models_router
from agent.api.providers import router as providers_router
from agent.api.active_project import router as active_project_router
from agent.api.ai import router as ai_router
from agent.worker.processor import get_worker_controller
from agent.services.flow_client import get_flow_client
from agent.services.event_bus import event_bus
from agent.sdk import init_sdk
from agent.logging_utils import configure_logging, log_event
from agent.services.backup import create_backup
from agent.services.readiness import check_readiness
from agent.api.errors import error_payload, ErrorResponse
from pydantic import BaseModel


class CallbackResponse(BaseModel):
    ok: bool
    reason: str | None = None

configure_logging()
logger = logging.getLogger(__name__)


# ─── WebSocket Server for Extension ─────────────────────────

async def ws_handler(websocket):
    """Handle a Chrome extension WebSocket connection."""
    client = get_flow_client()
    client.set_extension(websocket)
    log_event(logger, logging.INFO, "extension_connected", remote_address=str(websocket.remote_address))

    # Send callback secret so extension can authenticate HTTP callbacks
    await websocket.send(json.dumps({"type": "callback_secret", "secret": _CALLBACK_SECRET}))

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
                await client.handle_message(data, websocket)
            except json.JSONDecodeError:
                log_event(logger, logging.WARNING, "extension_message_invalid_json")
            except Exception as e:
                log_event(logger, logging.ERROR, "extension_message_failed", exc_info=True, error_type=type(e).__name__)
    except websockets.ConnectionClosed:
        pass
    finally:
        client.clear_extension(websocket)
        log_event(logger, logging.INFO, "extension_disconnected")


async def run_ws_server():
    """Run WebSocket server for extension connections."""
    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        logger.info("WebSocket server listening on ws://%s:%d", WS_HOST, WS_PORT)
        await asyncio.Future()  # run forever


async def run_backup_scheduler(interval_seconds: int):
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            result = await create_backup()
            log_event(logger, logging.INFO, "backup_completed", path=result["path"], files=result["files"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_event(logger, logging.ERROR, "backup_failed", exc_info=True, error_type=type(exc).__name__)


# ─── FastAPI App ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Load custom materials from DB into in-memory registry
    from agent.db.crud import list_materials as db_list_materials
    from agent.materials import register_material, _BUILTIN_IDS
    try:
        custom_materials = await db_list_materials()
        for m in custom_materials:
            if m["id"] not in _BUILTIN_IDS:
                register_material(m)
                logger.info("Loaded custom material from DB: %s", m["id"])
    except Exception as e:
        logger.warning("Failed to load custom materials: %s", e)

    ops = init_sdk(get_flow_client())
    startup_checks = await check_readiness()
    for name, check in startup_checks["checks"].items():
        if not check.get("ok"):
            logger.warning("Startup dependency unavailable: %s", name)
    logger.info("SDK initialized (OperationService ready)")
    from agent.services.ai import provider_status
    ai = provider_status()
    logger.info("AI content provider: %s", f"{ai['provider']} ({ai['model']}, configured={ai['configured']})" if ai["enabled"] else "disabled")
    logger.info("Flow Kit starting on %s:%d", API_HOST, API_PORT)

    controller = get_worker_controller()

    # SIGTERM handler for graceful shutdown (Unix only)
    try:
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, controller.request_shutdown)
    except (NotImplementedError, AttributeError):
        pass

    # Start background tasks
    ws_task = asyncio.create_task(run_ws_server())
    worker_task = asyncio.create_task(controller.start())
    backup_task = (
        asyncio.create_task(run_backup_scheduler(BACKUP_INTERVAL_SECONDS))
        if BACKUP_INTERVAL_SECONDS > 0 else None
    )
    log_event(logger, logging.INFO, "worker_started")

    yield

    controller.request_shutdown()
    await controller.drain()
    ws_task.cancel()
    worker_task.cancel()
    if backup_task:
        backup_task.cancel()
    await close_db()
    log_event(logger, logging.INFO, "worker_stopped")


app = FastAPI(title="Flow Kit", version="1.3.1", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=error_payload(
        "VALIDATION_ERROR", "Request validation failed", exc.errors()
    ))


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail
    message = detail if isinstance(detail, str) else "Request failed"
    return JSONResponse(status_code=exc.status_code, content=error_payload(
        f"HTTP_{exc.status_code}", message, None if isinstance(detail, str) else detail
    ), headers=exc.headers or {})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.exception("Unhandled API error path=%s", request.url.path)
    return JSONResponse(status_code=500, content=error_payload(
        "INTERNAL_ERROR", "An internal server error occurred"
    ))


@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 32 * 1024 * 1024:
        return JSONResponse(status_code=413, content=error_payload(
            "REQUEST_TOO_LARGE", "Request body exceeds the 32 MiB limit"
        ))
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_GENERATION_PATHS = {
    "/api/flow/generate-image",
    "/api/flow/generate-video",
    "/api/flow/generate-video-refs",
    "/api/flow/generate-video-omni",
    "/api/flow/generate-video-omni-text",
    "/api/flow/edit-image",
}


@app.middleware("http")
async def flow_caller_observability(request: Request, call_next):
    """Attribute generation submits without logging prompts, media or secrets."""
    response = await call_next(request)
    if request.method == "POST" and request.url.path in _GENERATION_PATHS:
        caller = (request.headers.get("x-flowkit-caller") or "unknown")[:80]
        logger.info(
            "Flow generation request caller=%s path=%s status=%s",
            caller,
            request.url.path,
            response.status_code,
        )
    return response


app.include_router(characters_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(videos_router, prefix="/api")
app.include_router(scenes_router, prefix="/api")
app.include_router(requests_router, prefix="/api")
app.include_router(flow_router, prefix="/api")
app.include_router(reviews_router, prefix="/api")
app.include_router(tts_router, prefix="/api")
app.include_router(materials_router, prefix="/api")
app.include_router(music_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(models_router)
app.include_router(providers_router)
app.include_router(active_project_router)


import secrets as _secrets
_CALLBACK_SECRET = _secrets.token_urlsafe(32)


@app.post("/api/ext/callback", response_model=CallbackResponse, responses={401: {"model": ErrorResponse}, 400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
async def ext_callback(request: Request):
    """HTTP callback for extension to deliver API responses.

    Replaces ws.send() for response delivery — immune to WS disconnect.
    Extension POSTs {id, status, data, error} here instead of sending via WS.
    Requires X-Callback-Secret header matching the secret sent to extension on WS connect.
    """
    supplied_secret = request.headers.get("X-Callback-Secret", "")
    if not hmac.compare_digest(supplied_secret, _CALLBACK_SECRET):
        raise HTTPException(401, "Invalid callback secret")
    try:
        data = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "Invalid JSON callback payload") from exc
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise HTTPException(422, "Callback payload requires a request id")
    client = get_flow_client()
    req_id = data.get("id")
    logger.info("ext/callback: id=%s pending=%d match=%s",
                str(req_id)[:8] if req_id else "none",
                client.pending_count,
                "yes" if req_id and client.has_pending(req_id) else "no")
    if req_id and client.deliver_response(data):
        return {"ok": True}
    return {"ok": False, "reason": "no matching pending request"}


@app.get("/health")
async def health():
    client = get_flow_client()
    return {
        "status": "ok",
        "version": app.version,
        "extension_connected": client.connected,
        "ws": client.ws_stats,
    }


@app.get("/ready")
async def ready():
    """Readiness for generation workloads, including the interactive bridge."""
    result = await check_readiness()
    status_code = 200 if result["ready"] else 503
    return JSONResponse(status_code=status_code, content=result)


# ─── Dashboard WebSocket ──────────────────────────────────────

@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    """WebSocket endpoint for dashboard clients (Chrome extension side panel)."""
    # Reject cross-origin connections (only allow localhost)
    origin = (websocket.headers.get("origin") or "").lower()
    if origin and not any(origin.startswith(p) for p in (
        "http://127.0.0.1", "http://localhost", "chrome-extension://",
    )):
        await websocket.close(code=4003, reason="Origin not allowed")
        return
    await websocket.accept()

    q = event_bus.subscribe()
    try:
        # Send initial snapshot
        client = get_flow_client()
        controller = get_worker_controller()
        from agent.db import crud
        pending_requests = await crud.list_requests(status="PENDING")
        processing_requests = await crud.list_requests(status="PROCESSING")
        snapshot = {
            "type": "snapshot",
            "health": {
                "status": "ok",
                "extension_connected": client.connected,
            },
            "requests": pending_requests + processing_requests,
            "worker": {
                "active": controller.active_count,
                "slots": max(0, 5 - controller.active_count),
            },
        }
        await websocket.send_text(json.dumps(snapshot))

        # Forward events from event_bus to this client
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=30.0)
                await websocket.send_text(msg)
            except asyncio.TimeoutError:
                # Send keepalive ping
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Dashboard WS client disconnected: %s", e)
    finally:
        event_bus.unsubscribe(q)


if __name__ == "__main__":
    import os
    import uvicorn
    reload_enabled = os.environ.get("GLA_RELOAD", "0") == "1"
    uvicorn.run(
        "agent.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=reload_enabled,
        reload_excludes=["*.db", "*.db-wal", "*.db-shm", "output/*"],
    )
