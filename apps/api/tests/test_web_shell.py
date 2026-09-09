"""Browser shell support: the bootstrap endpoint and same-origin SPA serving (D3).

The desktop shell hands the UI its port and token over Tauri IPC. A browser page has no IPC and is
served by this process, so it bootstraps over HTTP instead. These tests pin the parts that are easy
to break without noticing: which paths skip authentication, and that SPA routing can't be walked out
of the dist directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import PROTECTED_PATH_PREFIX, UNAUTHENTICATED_PATHS, require_http_token, requires_token


TOKEN = "s3cret-token"


def _app_with_auth() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def auth(request, call_next):
        return await require_http_token(request, call_next, lambda: TOKEN)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/bootstrap")
    async def bootstrap():
        return {"token": TOKEN, "version": "1.0.0", "mode": "web"}

    @app.get("/api/v1/private")
    async def private():
        return {"ok": True}

    return app


class TestAuthBoundary:
    def test_the_boundary_is_the_api_prefix(self):
        # The token protects the API, not the client bundle. Stating it as a prefix keeps it correct
        # as routers are added; changing it is a security decision that should fail a test first.
        assert PROTECTED_PATH_PREFIX == "/api/"
        assert set(UNAUTHENTICATED_PATHS) == {"/health", "/bootstrap"}

    @pytest.mark.parametrize("path", ["/api/v1/status", "/api/v1/leads", "/api/v1/runtime/x"])
    def test_api_paths_require_a_token(self, path: str):
        assert requires_token(path) is True

    @pytest.mark.parametrize(
        "path", ["/", "/index.html", "/pipeline", "/leads/abc123", "/assets/app.js", "/favicon.ico"]
    )
    def test_client_assets_and_spa_routes_do_not(self, path: str):
        # The regression this pins: token-gating these returned 401 for `/`, so the browser could
        # never load the page that would have fetched the token.
        assert requires_token(path) is False

    @pytest.mark.parametrize("path", ["/health", "/bootstrap"])
    def test_named_exemptions_do_not(self, path: str):
        assert requires_token(path) is False

    def test_bootstrap_is_reachable_without_a_token(self):
        # It has to be: it is how the browser page obtains the token in the first place.
        res = TestClient(_app_with_auth()).get("/bootstrap")
        assert res.status_code == 200
        assert res.json()["token"] == TOKEN

    def test_everything_else_still_requires_the_token(self):
        client = TestClient(_app_with_auth())
        assert client.get("/api/v1/private").status_code == 401
        assert client.get("/api/v1/private", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200

    def test_a_wrong_token_is_rejected(self):
        client = TestClient(_app_with_auth())
        assert client.get("/api/v1/private", headers={"Authorization": "Bearer nope"}).status_code == 401


class TestSpaServing:
    """The `/{path:path}` fallback resolves caller-supplied paths, so containment matters."""

    @pytest.fixture
    def served(self, tmp_path: Path) -> TestClient:
        """Mount the **real** `_mount_web_ui` over a throwaway dist.

        This fixture used to re-declare the fallback route inline. That made the containment
        assertions below pass against a hand-written copy while the production route behaved
        differently — it returned the SPA shell, with status 200, for any unmatched /api/ path. The
        duplicate is gone: `dist` is injectable precisely so these tests bind to the shipped code.
        """
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><title>shell</title>")
        (dist / "assets" / "app.js").write_text("console.log('app')")
        (tmp_path / "secret.txt").write_text("NOT SERVABLE")

        from api.app import _mount_web_ui

        app = FastAPI()
        _mount_web_ui(app, dist)
        return TestClient(app)

    def test_serves_the_shell_at_root(self, served: TestClient):
        assert "shell" in served.get("/").text

    def test_serves_a_real_asset(self, served: TestClient):
        assert "console.log" in served.get("/assets/app.js").text

    def test_deep_links_fall_back_to_the_shell(self, served: TestClient):
        # /pipeline is a client-side route with no file behind it; a 404 would break refresh.
        assert "shell" in served.get("/pipeline").text
        assert "shell" in served.get("/leads/abc123").text

    @pytest.mark.parametrize(
        "path", ["../secret.txt", "..%2Fsecret.txt", "foo/../../secret.txt", "/etc/passwd"]
    )
    def test_cannot_escape_the_dist_directory(self, served: TestClient, path: str):
        # The fallback must never hand out a file from outside dist, no matter what was asked for.
        res = served.get(f"/{path}")
        assert "NOT SERVABLE" not in res.text
        assert "root:" not in res.text

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/does-not-exist",
            "/api/corpus/stats",   # the real route is /api/v1/corpus/stats — the typo that found this
            "/api/",
        ],
    )
    def test_unmatched_api_paths_404_instead_of_returning_the_shell(
        self, served: TestClient, path: str
    ):
        """An API client must never receive `200 text/html` for a missing endpoint.

        The SPA route matches everything and is registered last, so before this it answered every
        unrouted /api/ call with the HTML shell and a success status. A caller with a stale or
        mistyped path saw 200, tried to parse HTML as JSON, and got an error pointing nowhere near
        the actual cause.
        """
        res = served.get(path)
        assert res.status_code == 404, f"{path} returned {res.status_code}"
        assert "shell" not in res.text

    def test_client_routes_that_merely_start_with_api_are_unaffected(self, served: TestClient):
        # The guard keys on the "/api/" prefix, so a client route like /apiary must still resolve
        # to the shell. Matching on "/api" without the trailing slash would break it.
        assert "shell" in served.get("/apiary").text
        assert "shell" in served.get("/api-docs-guide").text
