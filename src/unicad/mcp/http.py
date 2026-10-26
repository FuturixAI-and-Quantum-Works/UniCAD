"""Streamable HTTP transport: the MCP endpoint at /mcp, bearer-token auth and per-tenant workspaces."""

import hmac
import json
import re
from collections import OrderedDict
from typing import Any

import anyio
import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from unicad.mcp.workspace import current_tenant

TENANT_HEADER = "X-UniCAD-Tenant"
TENANT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
MCP_PATH = "/mcp"
HEALTH_PATH = "/healthz"
MAX_TRACKED_SESSIONS = 10_000


class GateError(Exception):
    def __init__(self, status: int, message: str, headers: dict[str, str] | None = None) -> None:
        self.status, self.message, self.headers = status, message, headers or {}


def _json_response(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[Message, Message]:
    payload = json.dumps(body).encode()
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    raw += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return {"type": "http.response.start", "status": status, "headers": raw}, {"type": "http.response.body", "body": payload}


class GateMiddleware:
    """Checks the bearer token, resolves the tenant and binds it for the request.

    A session belongs to the tenant that initialised it; a later request for that session with another tenant is refused."""

    def __init__(self, app: ASGIApp, token: str | None, tenants: bool) -> None:
        self.app = app
        self.token = None if token is None else token.encode()
        self.tenants = tenants
        self.sessions: OrderedDict[str, str | None] = OrderedDict()

    def _authorise(self, headers: dict[bytes, bytes]) -> None:
        if self.token is None:
            return
        scheme, _, value = headers.get(b"authorization", b"").partition(b" ")
        if scheme.lower() != b"bearer" or not hmac.compare_digest(value.strip(), self.token):
            raise GateError(401, "missing or invalid bearer token", {"WWW-Authenticate": "Bearer"})

    def _tenant(self, headers: dict[bytes, bytes]) -> str | None:
        if not self.tenants:
            return None
        tenant = headers.get(TENANT_HEADER.lower().encode(), b"").decode("latin-1")
        if not TENANT_PATTERN.fullmatch(tenant):
            raise GateError(400, f"{TENANT_HEADER} must be a slug of 1-63 characters: lowercase letters, digits, '-' or '_', starting with a letter or digit")
        return tenant

    def _bind(self, headers: dict[bytes, bytes], tenant: str | None) -> None:
        sid = headers.get(b"mcp-session-id", b"").decode("latin-1")
        if sid and sid in self.sessions and self.sessions[sid] != tenant:
            raise GateError(403, f"this session belongs to another tenant; send the {TENANT_HEADER} it was opened with")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"] == HEALTH_PATH:
            for message in _json_response(200, {"status": "ok"}):
                await send(message)
            return
        headers = dict(scope["headers"])
        try:
            self._authorise(headers)
            tenant = self._tenant(headers)
            self._bind(headers, tenant)
        except GateError as e:
            for message in _json_response(e.status, {"error": e.message}, e.headers):
                await send(message)
            return

        async def remember(message: Message) -> None:
            if message["type"] == "http.response.start":
                for k, v in message["headers"]:
                    if k.lower() == b"mcp-session-id":
                        self.sessions[v.decode("latin-1")] = tenant
                        while len(self.sessions) > MAX_TRACKED_SESSIONS:
                            self.sessions.popitem(last=False)
            await send(message)

        reset = current_tenant.set(tenant)
        try:
            await self.app(scope, receive, remember)
        finally:
            current_tenant.reset(reset)


def build_http_app(server: MCPServer, token: str | None, tenants: bool = False, stateless: bool = False, host: str = "127.0.0.1") -> Starlette:
    app = server.streamable_http_app(streamable_http_path=MCP_PATH, stateless_http=stateless, host=host)
    app.add_middleware(GateMiddleware, token=token, tenants=tenants)
    return app


def serve(app: Starlette, host: str, port: int) -> None:
    anyio.run(uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info")).serve)
