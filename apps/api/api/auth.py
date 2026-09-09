from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import Request, WebSocket, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer


LOCAL_ORIGIN_RE = r"^(tauri://localhost|https?://(localhost|127\.0\.0\.1|tauri\.localhost|\[::1\])(?::\d+)?)$"

_bearer = HTTPBearer(auto_error=False)


def create_api_token() -> str:
    return secrets.token_hex(32)


def valid_token(candidate: str, expected: str) -> bool:
    return bool(candidate) and bool(expected) and secrets.compare_digest(candidate, expected)


# The bearer token protects the **API**, not the client bundle.
#
# Every API router in this app mounts under `/api/`, so that prefix is the authentication boundary.
# Everything else is either a liveness probe or the browser client's own HTML/JS/CSS, which cannot
# be token-protected for a simple ordering reason: the page has to load *before* it can ask for a
# token, so requiring one to fetch `index.html` locks the browser out of the app entirely. (That is
# not hypothetical — a path allowlist here returned 401 for `/` and every SPA deep link.)
#
# Stating it as a protected *prefix* rather than a public allowlist also keeps it correct as the app
# grows: a new router under `/api/v1` is protected automatically, and a new static asset is public
# automatically, with no list to forget to update.
PROTECTED_PATH_PREFIX = "/api/"

# Reachable without a token even though they sit under the protected prefix, or need naming
# explicitly:
#   /health     liveness, deliberately unauthenticated so a supervisor can probe it
#   /bootstrap  how the browser-served UI *obtains* the token — the same chicken-and-egg the
#               desktop shell solves with `invoke("get_api_token")`
#
# `/bootstrap` handing out the token is only acceptable because of two properties, and both must
# keep holding: the server binds loopback only (TrustedHostMiddleware also rejects non-loopback Host
# headers), and CORS limits reads to local origins so no other website can fetch it. The residual
# exposure is other local processes, which is identical to the desktop path — a local process can
# obtain the token either way. Never bind 0.0.0.0.
UNAUTHENTICATED_PATHS = frozenset({"/health", "/bootstrap"})


def requires_token(path: str) -> bool:
    """Whether a request path must present a valid bearer token."""
    if path in UNAUTHENTICATED_PATHS:
        return False
    return path.startswith(PROTECTED_PATH_PREFIX)


async def require_http_token(request: Request, call_next, token_getter: Callable[[], str]):
    if request.method == "OPTIONS" or not requires_token(request.url.path):
        return await call_next(request)

    creds = await _bearer(request)
    if creds is None or not valid_token(creds.credentials, token_getter()):
        return JSONResponse(
            {"detail": "invalid token"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return await call_next(request)


WS_TOKEN_SUBPROTOCOL = "jhm.bearer"


def ws_token_from_subprotocol(ws: WebSocket) -> str:
    """Extract the bearer token offered as the 2nd WebSocket subprotocol.

    Browsers can't set custom WS headers, but they can offer subprotocols, which
    travel in the ``Sec-WebSocket-Protocol`` *header* (not the URL). The client
    offers ``["jhm.bearer", "<token>"]``; we read the token from there.
    """
    raw = ws.headers.get("sec-websocket-protocol", "")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) >= 2 and parts[0] == WS_TOKEN_SUBPROTOCOL:
        return parts[1]
    return ""


async def require_ws_token(ws: WebSocket, token_getter: Callable[[], str]) -> bool:
    expected = token_getter()

    # Preferred (browser-safe): token in the Sec-WebSocket-Protocol header.
    if valid_token(ws_token_from_subprotocol(ws), expected):
        return True

    # Non-browser clients (tests/tools): Authorization header.
    auth = ws.headers.get("authorization", "")
    if auth.startswith("Bearer ") and valid_token(auth[7:], expected):
        return True

    await ws.close(code=4401, reason="invalid token")
    return False
