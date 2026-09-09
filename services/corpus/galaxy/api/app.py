"""FastAPI gateway (docs/07).

Middleware chain: logging (request id + timing) → routers. API-key auth is a per-router
dependency. Health is unauthenticated; everything else requires x-api-key.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from galaxy.api.routers import health, ingest, llm, pipeline, profile, search, tailor
from galaxy.common.config import get_settings

# the built web client (docs/11) — served at / so http://127.0.0.1:8000 IS the website
_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("api.startup", run_role=settings.run_role)
    yield
    log.info("api.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="GalaxyHire Corpus API", version="0.1.0", lifespan=lifespan)

    # The browser extension (chrome-extension:// origin) calls this API from its side panel.
    # Auth is a header (x-api-key), not cookies, so a wildcard origin with credentials off is safe
    # for a local-first dev tool and removes the usual "extension can't connect" CORS friction.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def logging_mw(request: Request, call_next):
        req_id = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
        start = time.perf_counter()
        structlog.contextvars.bind_contextvars(req_id=req_id, path=request.url.path)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("api.unhandled")
            structlog.contextvars.clear_contextvars()
            return JSONResponse(status_code=500, content={"error": "internal", "req_id": req_id})
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        log.info("api.request", status=response.status_code, ms=elapsed_ms)
        response.headers["x-request-id"] = req_id
        structlog.contextvars.clear_contextvars()
        return response

    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(llm.router)
    app.include_router(profile.router)
    app.include_router(search.router)
    app.include_router(tailor.router)
    app.include_router(pipeline.router)

    # Mount the web client LAST so API routes win; only when it's been built (docs/11 §W2).
    if _WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="web")
    return app


app = create_app()
