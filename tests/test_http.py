"""Streamable HTTP transport: bearer-token auth, tenant workspaces and the console entry point, against a real server process."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

TOKEN = "s3cret-test-token"
CREATE = {"name": "Tenant Roof", "client": "Test Client", "segment": "ci_rooftop", "site": "Test roof", "grid_voltage_v": 415}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, workspace, *flags: str, env: dict[str, str] | None = None) -> None:
        self.workspace = workspace
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        environ = {**os.environ, "UNICAD_WORKSPACE": str(workspace), "UNICAD_TOKEN": TOKEN} | (env or {})
        self.log = open(workspace.parent / f"server-{self.port}.log", "w+", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "unicad", "--http", "--port", str(self.port), *flags], env=environ, stdout=self.log, stderr=subprocess.STDOUT
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early:\n{self.output()}")
            try:
                if httpx2.get(f"{self.url}/healthz", timeout=1).status_code == 200:
                    return
            except httpx2.TransportError:
                time.sleep(0.1)
        raise RuntimeError(f"server did not start:\n{self.output()}")

    def output(self) -> str:
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()

    @asynccontextmanager
    async def session(self, tenant: str | None = None, token: str | None = TOKEN):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if tenant is not None:
            headers["X-UniCAD-Tenant"] = tenant
        async with httpx2.AsyncClient(headers=headers, timeout=30) as http, streamable_http_client(f"{self.url}/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                yield Client(session, await session.initialize())


class Client:
    def __init__(self, session: ClientSession, init) -> None:
        self.session = session
        self.init = init

    async def call(self, tool: str, /, **args):
        res = await self.session.call_tool(tool, args)
        assert not res.is_error, res.content[0].text
        return res.structured_content


@pytest.fixture(scope="module")
def tenant_server(tmp_path_factory):
    server = Server(tmp_path_factory.mktemp("tenants") / "ws", "--tenants")
    yield server
    server.stop()


@pytest.fixture(scope="module")
def stateless_server(tmp_path_factory):
    server = Server(tmp_path_factory.mktemp("stateless") / "ws", "--tenants", "--stateless")
    yield server
    server.stop()


@pytest.fixture(scope="module")
def plain_server(tmp_path_factory):
    server = Server(tmp_path_factory.mktemp("plain") / "ws")
    yield server
    server.stop()


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 120))


def post(server: Server, headers: dict[str, str], body: dict | None = INIT) -> httpx2.Response:
    return httpx2.post(f"{server.url}/mcp", headers=MCP_HEADERS | headers, content=json.dumps(body), timeout=30)


def test_missing_or_wrong_token_is_401_and_right_token_works(plain_server):
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": f"Basic {TOKEN}"}, {"Authorization": "Bearer"}):
        r = post(plain_server, headers)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer", headers
        assert TOKEN not in r.text
    ok = post(plain_server, {"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200 and "unicad" in ok.text


def test_auth_applies_to_every_method_and_health_is_open(plain_server):
    assert httpx2.get(f"{plain_server.url}/mcp", timeout=10).status_code == 401
    assert httpx2.delete(f"{plain_server.url}/mcp", timeout=10).status_code == 401
    health = httpx2.get(f"{plain_server.url}/healthz", timeout=10)
    assert health.status_code == 200 and health.json() == {"status": "ok"}


def test_sdk_client_runs_the_workflow_over_http(plain_server):
    async def go():
        async with plain_server.session() as c:
            tools = {t.name for t in (await c.session.list_tools()).tools}
            assert {"project_create", "site_import", "layout_place", "network_autobuild", "validate", "cable_schedule"} <= tools
            created = await c.call("project_create", **CREATE)
            assert created["revision"] == 1
            assert (await c.call("project_list"))["projects"][0]["project_id"] == created["project_id"]
            prompts = {p.name for p in (await c.session.list_prompts()).prompts}
            assert {"new_rooftop_project", "revise_after_comments"} <= prompts
            assert "network_autobuild" in c.init.instructions

    run(go())
    assert (plain_server.workspace / "tenant-roof" / "revisions" / "1.json").is_file()


def leaves(exc: BaseException) -> list[BaseException]:
    return [x for e in exc.exceptions for x in leaves(e)] if isinstance(exc, BaseExceptionGroup) else [exc]


def test_sdk_client_without_token_cannot_connect(plain_server):
    async def go():
        async with plain_server.session(token=None):
            pass

    with pytest.raises(BaseExceptionGroup) as err:
        run(go())
    assert [type(x).__name__ for x in leaves(err.value)] == ["MCPError"]


def test_plain_server_ignores_the_tenant_header(plain_server):
    async def go():
        async with plain_server.session(tenant="anything") as c:
            return (await c.call("project_list"))["workspace"]

    assert run(go()) == str(plain_server.workspace)


@pytest.mark.parametrize("which", ["tenant_server", "stateless_server"])
def test_tenants_get_separate_workspaces(which, request):
    server: Server = request.getfixturevalue(which)

    async def go():
        async with server.session("acme") as a, server.session("globex") as g:
            await a.call("project_create", **CREATE)
            assert (await g.call("project_list"))["projects"] == []
            await g.call("project_create", **CREATE | {"name": "Tenant Roof", "client": "Other Client"})
            await asyncio.gather(
                a.call("project_snapshot", project_id="tenant-roof", label="a-only"),
                g.call("project_snapshot", project_id="tenant-roof", label="g-only"),
            )
            return await a.call("project_get", project_id="tenant-roof"), await g.call("project_get", project_id="tenant-roof")

    acme, globex = run(go())
    assert acme["client"] == "Test Client" and globex["client"] == "Other Client"
    root = server.workspace
    assert (root / "acme" / "tenant-roof" / "revisions" / "1.json").is_file()
    assert (root / "globex" / "tenant-roof" / "revisions" / "1.json").is_file()
    assert json.loads((root / "acme" / "tenant-roof" / "snapshots.json").read_text(encoding="utf-8")) == {"a-only": 1}
    assert json.loads((root / "globex" / "tenant-roof" / "snapshots.json").read_text(encoding="utf-8")) == {"g-only": 1}
    assert not (root / "tenant-roof").exists()


def test_tenant_header_is_required_and_validated(tenant_server):
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert post(tenant_server, auth).status_code == 400
    for bad in ("..", "../x", "a/b", "UPPER", "-lead", "", "x" * 64, "a b"):
        r = httpx2.post(
            f"{tenant_server.url}/mcp",
            headers=MCP_HEADERS | auth | {"X-UniCAD-Tenant": bad},
            content=json.dumps(INIT),
            timeout=30,
        )
        assert r.status_code == 400, bad
    assert post(tenant_server, auth | {"X-UniCAD-Tenant": "ok-tenant_1"}).status_code == 200
    assert not [p for p in tenant_server.workspace.parent.iterdir() if p.name not in {"ws"} and p.is_dir()]


def test_a_session_cannot_be_used_by_another_tenant(tenant_server):
    auth = {"Authorization": f"Bearer {TOKEN}"}
    opened = post(tenant_server, auth | {"X-UniCAD-Tenant": "acme"})
    sid = opened.headers["mcp-session-id"]
    follow = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    stolen = post(tenant_server, auth | {"X-UniCAD-Tenant": "globex", "Mcp-Session-Id": sid}, follow)
    assert stolen.status_code == 403
    assert post(tenant_server, auth | {"X-UniCAD-Tenant": "acme", "Mcp-Session-Id": sid}, follow).status_code == 200


def test_http_requires_a_token_unless_told_otherwise(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "UNICAD_TOKEN"}
    out = subprocess.run([sys.executable, "-m", "unicad", "--http", "--port", str(free_port())], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "UNICAD_TOKEN" in out.stderr


def test_no_auth_flag_serves_without_a_token(tmp_path):
    server = Server(tmp_path / "ws", "--no-auth", env={"UNICAD_TOKEN": ""})
    try:
        assert post(server, {}).status_code == 200
    finally:
        server.stop()


def test_help_lists_http_options_and_environment():
    out = subprocess.run([sys.executable, "-m", "unicad", "--help"], capture_output=True, text=True, timeout=60).stdout
    for needle in ("--http", "--host", "--port", "--tenants", "UNICAD_TOKEN", "LIBRECAD", "LIBREDWG_BIN", "DISPLAY"):
        assert needle in out, needle
