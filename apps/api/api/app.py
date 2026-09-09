from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.auth import LOCAL_ORIGIN_RE, PROTECTED_PATH_PREFIX, require_http_token
from api.routers import apply as apply_router
from api.routers import conflicts as conflicts_router
from api.routers import documents as documents_router
from api.routers import doc_selections as doc_selections_router
from api.routers import misc_context as misc_context_router
from api.routers import aicheck as aicheck_router
from api.routers import email as email_router
from api.routers import diagnostics, discovery, events, generation, health, ingestion, latex, leads, misc, profile, runtime, settings, templates
from api.websocket import register_websocket
from core.telemetry import record_exception
from core.version import APP_VERSION


def create_app(
    *,
    lifespan,
    token_getter: Callable[[], str],
    started_at: float,
    scheduler=None,
    ghost_tick=None,
    connection_manager=None,
    logger=None,
    websocket_token_guard=None,
) -> FastAPI:
    app = FastAPI(
        title="GalaxyHire",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=LOCAL_ORIGIN_RE,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Defense-in-depth against DNS rebinding: the sidecar only ever serves the
    # local Tauri webview, so reject requests whose Host header isn't loopback.
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "::1"],
    )
    app.state.connection_manager = connection_manager
    app.state.token_getter = token_getter

    @app.middleware("http")
    async def require_http_token_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id", "")
        try:
            response = await require_http_token(request, call_next, token_getter)
            if request_id:
                response.headers["x-request-id"] = request_id
            return response
        except Exception as exc:
            record_exception(exc, domain="api", request_id=request_id, path=request.url.path)
            raise

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception):
        # Never leak internal exception text to the client; the middleware above
        # already recorded the detail server-side. Return a generic 500 + the
        # request id so a user-reported failure can be correlated to the log.
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request.headers.get("x-request-id", "")},
        )

    app.include_router(health.create_router(started_at))

    @app.get("/bootstrap")
    async def bootstrap() -> dict:
        """What the browser-served UI needs before it can authenticate.

        The desktop shell gets port and token over Tauri IPC. A browser page has no IPC, and it is
        served by this very process, so it asks here instead. Exempt from bearer auth by necessity —
        see `UNAUTHENTICATED_PATHS` in api/auth.py for why that is safe and what must stay true.
        """
        return {"token": token_getter(), "version": APP_VERSION, "mode": "web"}
    app.include_router(diagnostics.create_router(started_at))
    app.include_router(events.router)
    app.include_router(misc.router)
    app.include_router(runtime.router)
    app.include_router(profile.router)
    if connection_manager is not None:
        app.include_router(leads.create_router(connection_manager))
    if scheduler is not None and ghost_tick is not None:
        app.include_router(settings.create_router(scheduler, ghost_tick))
    if connection_manager is not None and logger is not None:
        app.include_router(ingestion.create_router(connection_manager, logger))
    app.include_router(templates.create_router(logger))
    app.include_router(documents_router.create_router(logger))
    app.include_router(misc_context_router.create_router(logger))
    app.include_router(conflicts_router.create_router())
    app.include_router(doc_selections_router.create_router())
    app.include_router(aicheck_router.create_router())
    app.include_router(latex.create_router())
    app.include_router(apply_router.create_router(connection_manager))
    app.include_router(email_router.create_router(connection_manager))
    if connection_manager is not None and logger is not None:
        app.include_router(discovery.create_router(manager=connection_manager, logger=logger))
    if connection_manager is not None:
        app.include_router(generation.create_router(manager=connection_manager))
    if connection_manager is not None and logger is not None and websocket_token_guard is not None:
        register_websocket(
            app,
            token_guard=websocket_token_guard,
            manager=connection_manager,
            started_at=started_at,
            logger=logger,
        )

    # Serve the built UI at / so the browser shell is same-origin with its API (ARCHITECTURE.md
    # D3). Mounted last: a mount at "/" is greedy, and anything registered after it would be
    # shadowed by the SPA fallback. Absent in development, where Vite serves the UI and proxies
    # here, and absent until `npm run build` has produced dist/.
    _mount_web_ui(app)

    return app


def _mount_web_ui(app: FastAPI, dist: Path | None = None) -> None:
    """Mount the built SPA. `dist` is injectable so tests exercise *this* handler.

    The suite previously reimplemented the fallback inline, which meant it was asserting against a
    copy — the containment checks passed while the real route quietly returned the shell for
    unmatched /api/ paths. A test that duplicates the code under test cannot catch it drifting.
    """
    from fastapi.staticfiles import StaticFiles

    dist = dist or Path(__file__).resolve().parents[2] / "web" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        """Serve a real file when one exists, otherwise index.html.

        The UI is a single-page app, so a deep link like /pipeline is a client route with no file
        behind it and must still return the shell rather than a 404.
        """
        # ...but never for API paths. This route is registered last and matches everything, so an
        # unmatched /api/ request would otherwise get `200 text/html` — the SPA shell — instead of
        # a 404. A client calling a renamed, mistyped or removed endpoint then sees a success
        # status and an HTML body where JSON was expected, and the real error surfaces later as a
        # confusing parse failure. Cost real time when /api/corpus/stats (the route is
        # /api/v1/corpus/stats) silently returned the UI.
        if f"/{path}".startswith(PROTECTED_PATH_PREFIX):
            raise HTTPException(status_code=404, detail=f"no such endpoint: /{path}")

        candidate = (dist / path).resolve()
        # Containment check: `path` is caller-supplied, so `../` sequences must not be able to
        # serve files from outside dist.
        if path and dist in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
