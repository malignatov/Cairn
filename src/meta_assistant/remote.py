"""HTTP ("remote") serving for the macOS menu-bar app.

This exposes Cairn's streamable-HTTP MCP app with an *optional* bearer-token
gate, plus a start/stoppable controller the menu-bar app drives.

Auth, honestly: ChatGPT and claude.ai web only support OAuth (or no auth) for
remote MCP connectors — they cannot present a static bearer token or custom
header. So this token gate will NOT let those hosted apps in. What it does:

  - block random scanners that find a tunnelled endpoint, and
  - authenticate header-capable clients you control (Claude Code, curl, the
    MCP Inspector, the Claude *Messages API* `authorization_token`).

Real hosted-client auth is OAuth 2.1 (the MCP authorization spec), which is a
substantial server and deliberately out of scope here. The safe default is to
serve on localhost and treat any remote exposure as a deliberate act.
"""

from __future__ import annotations

import logging
import secrets
import threading
from typing import Optional

log = logging.getLogger("meta_assistant.remote")


# --- optional bearer-token gate -----------------------------------------------


class BearerAuthMiddleware:
    """Pure-ASGI middleware requiring ``Authorization: Bearer <token>`` on HTTP
    requests. Pure ASGI (no Starlette dependency) so it wraps whatever the MCP
    SDK returns; non-HTTP scopes (notably ``lifespan``) pass straight through so
    the session-manager startup still runs. Constant-time comparison."""

    def __init__(self, app, token: str):
        self.app = app
        self._expected = b"Bearer " + token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        provided = dict(scope.get("headers") or []).get(b"authorization", b"")
        if not secrets.compare_digest(provided, self._expected):
            await self._unauthorized(send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _unauthorized(send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b'Bearer realm="cairn"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})


def build_http_app(token: Optional[str] = None):
    """Build the streamable-HTTP MCP ASGI app, optionally gated by ``token``."""
    from .server import build_server

    mcp, _storage = build_server()
    app = mcp.streamable_http_app()
    if token:
        app = BearerAuthMiddleware(app, token)
    return app


def generate_token() -> str:
    """A fresh URL-safe token for the app to offer when the user enables auth."""
    return secrets.token_urlsafe(24)


# --- serving ------------------------------------------------------------------


def _display_url(host: str, port: int) -> str:
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::1") else host
    return f"http://{shown}:{port}/mcp"


def serve(host: str = "127.0.0.1", port: int = 8000, token: Optional[str] = None) -> None:
    """Blocking: run the HTTP MCP server with uvicorn (headless, e.g. testing)."""
    import uvicorn

    log.info("serving Cairn on %s%s", _display_url(host, port), " [token required]" if token else "")
    uvicorn.run(build_http_app(token), host=host, port=port, log_level="info")


class ServerController:
    """Run the uvicorn server on a background thread so the Cocoa run loop can
    own the main thread. ``stop()`` asks uvicorn to exit cleanly (no signals —
    those only install on the main thread)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000, token: Optional[str] = None):
        self.host = host
        self.port = port
        self.token = token
        self._server = None
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def url(self) -> str:
        return _display_url(self.host, self.port)

    def start(self) -> None:
        if self.running:
            return
        import uvicorn

        config = uvicorn.Config(
            build_http_app(self.token), host=self.host, port=self.port, log_level="info"
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # not on the main thread
        self._server = server
        self._thread = threading.Thread(target=server.run, name="cairn-http", daemon=True)
        self._thread.start()
        log.info("server thread started on %s", self.url)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        log.info("server stopped")
