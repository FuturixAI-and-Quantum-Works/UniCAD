# Install and connect

UniCAD is one MCP server, `unicad-mcp`, with two transports: stdio for desktop clients and Streamable HTTP for your own agent app. Drawings are rendered by LibreCAD 2.2.1.6, DWG files by LibreDWG; both are optional until you ask for PDF or DWG output (DXF, XLSX and every design tool work without them).

## Requirements

| Needed for | What | Notes |
| --- | --- | --- |
| Everything | Python 3.12+ and [uv](https://docs.astral.sh/uv/) | `uvx` runs the server without a manual install. |
| PDF output | LibreCAD 2.2.1.6 | Point `LIBRECAD` at the executable or at an extracted AppImage's `AppRun`. Other versions are not tested. |
| PDF output on a server | An X display | LibreCAD needs one even when it renders headless. Set `DISPLAY`, or install `xvfb`: UniCAD then starts `xvfb-run -a` itself when `DISPLAY` is unset. |
| DWG output | LibreDWG 0.13.x | Set `LIBREDWG_BIN` to the directory holding `dxf2dwg` and `dwg2dxf`. |
| Page rasters | poppler-utils | `pdftoppm`. |

Getting the LibreCAD AppImage without FUSE:

```sh
curl -fLO https://github.com/LibreCAD/LibreCAD/releases/download/v2.2.1.6/LibreCAD-v2.2.1.6-x86_64.AppImage
chmod +x LibreCAD-v2.2.1.6-x86_64.AppImage
./LibreCAD-v2.2.1.6-x86_64.AppImage --appimage-extract
export LIBRECAD=$PWD/squashfs-root/AppRun
```

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `UNICAD_WORKSPACE` | Directory holding the projects (`<id>/revisions/<n>.json`) and their exports. | `./unicad-workspace` |
| `UNICAD_CATALOG` | Catalog directory override (module, inverter, cable, ... JSON files). | The catalog shipped with the package. |
| `UNICAD_PROFILES` | Extra company-profile directories, separated by `:`. | The profiles shipped with the package. |
| `LIBRECAD` | LibreCAD executable or `AppRun`. | `librecad` on `PATH`. |
| `LIBREDWG_BIN` | Directory with `dxf2dwg` and `dwg2dxf`. | `PATH`. |
| `UNICAD_TOKEN` | Bearer token the HTTP server requires. | None: `--http` refuses to start without it unless `--no-auth` is given. |
| `DISPLAY` | X display for LibreCAD. | Unset: `xvfb-run -a` is used when installed. |

The same workspace and catalog can be set with `--workspace` and `--catalog`.

## Stdio clients

Every snippet runs the server from a checkout at `/path/to/CAD-Agent`. Replace the paths. `uvx --from /path/to/CAD-Agent unicad-mcp --help` checks that the command works.

### Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "unicad": {
      "command": "uvx",
      "args": ["--from", "/path/to/CAD-Agent", "unicad-mcp"],
      "env": {
        "UNICAD_WORKSPACE": "/home/me/unicad-projects",
        "LIBRECAD": "/opt/librecad/squashfs-root/AppRun",
        "LIBREDWG_BIN": "/opt/libredwg/bin"
      }
    }
  }
}
```

On a host without a desktop session, use `"command": "xvfb-run"` and `"args": ["-a", "uvx", "--from", "/path/to/CAD-Agent", "unicad-mcp"]`.

### Claude Code

```sh
claude mcp add unicad \
  -e UNICAD_WORKSPACE=$HOME/unicad-projects -e LIBRECAD=/opt/librecad/squashfs-root/AppRun \
  -- uvx --from /path/to/CAD-Agent unicad-mcp
```

For a team, put the same server in the project's `.mcp.json`:

```json
{
  "mcpServers": {
    "unicad": {
      "command": "uvx",
      "args": ["--from", "/path/to/CAD-Agent", "unicad-mcp"],
      "env": { "UNICAD_WORKSPACE": "./unicad-workspace" }
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project):

```json
{
  "mcpServers": {
    "unicad": {
      "command": "uvx",
      "args": ["--from", "/path/to/CAD-Agent", "unicad-mcp"],
      "env": { "UNICAD_WORKSPACE": "/home/me/unicad-projects", "LIBRECAD": "/opt/librecad/squashfs-root/AppRun" }
    }
  }
}
```

### OpenCode

`opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "unicad": {
      "type": "local",
      "command": ["uvx", "--from", "/path/to/CAD-Agent", "unicad-mcp"],
      "environment": {
        "UNICAD_WORKSPACE": "/home/me/unicad-projects",
        "LIBRECAD": "/opt/librecad/squashfs-root/AppRun"
      },
      "enabled": true
    }
  }
}
```

## HTTP for your own agent app

```sh
UNICAD_TOKEN=$(openssl rand -hex 24) unicad-mcp --http --host 127.0.0.1 --port 8765
```

The endpoint is `http://127.0.0.1:8765/mcp` (Streamable HTTP); `GET /healthz` answers `{"status": "ok"}` without a token for load balancers.

- **Auth.** Every request to `/mcp` needs `Authorization: Bearer $UNICAD_TOKEN`. A missing or wrong token gets `401` with `WWW-Authenticate: Bearer`. The token is compared in constant time. Serve over TLS (a reverse proxy) outside localhost; the server speaks plain HTTP.
- **Tenants.** `--tenants` gives each caller its own workspace: the `X-UniCAD-Tenant` header names a subdirectory of `UNICAD_WORKSPACE` and is required. It must be a slug of 1 to 63 characters: lowercase letters, digits, `-` or `_`, starting with a letter or digit. Anything else gets `400`. A session belongs to the tenant that opened it; using its session id with another tenant gets `403`. The tenant is an isolation boundary for projects and exports, not an authentication method: your app decides which tenant a user may name, and the shared token proves the caller is your app.
- **Sessions.** Sessions are kept in the server's memory. `--stateless` makes every request stand alone, which suits several replicas behind a load balancer.

Python client with the MCP SDK:

```python
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

headers = {"Authorization": f"Bearer {token}", "X-UniCAD-Tenant": "acme"}
async with httpx2.AsyncClient(headers=headers, timeout=60) as http:
    async with streamable_http_client("http://127.0.0.1:8765/mcp", http_client=http) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            created = await session.call_tool("project_create", {"name": "Pune MIDC factory roof", "client": "Demo Industries", "segment": "ci_rooftop", "site": "Plot B-12", "grid_voltage_v": 415})
```

Claude Code and OpenCode can also connect to it directly:

```sh
claude mcp add --transport http unicad http://127.0.0.1:8765/mcp --header "Authorization: Bearer $UNICAD_TOKEN"
```

```json
{ "mcp": { "unicad": { "type": "remote", "url": "http://127.0.0.1:8765/mcp", "headers": { "Authorization": "Bearer {env:UNICAD_TOKEN}" } } } }
```

## Docker

The image holds the server, LibreCAD 2.2.1.6 (official AppImage, extracted, no FUSE), Xvfb with xauth, LibreDWG 0.13.4 built from the GNU tarball, and poppler-utils. Both downloads are checked against pinned SHA-256 sums. The entrypoint starts a private Xvfb on `:99` before the server and the container runs as a non-root user.

```sh
docker build -t unicad-mcp .
docker run -d --name unicad -e UNICAD_TOKEN=change-me -p 8765:8765 -v unicad-data:/data unicad-mcp
docker run -d -e UNICAD_TOKEN=change-me -p 8765:8765 -v unicad-data:/data unicad-mcp --http --host 0.0.0.0 --port 8765 --tenants
```

The default command is `--http --host 0.0.0.0 --port 8765`; arguments you pass replace it. Projects and exports live in the `/data` volume. Without `UNICAD_TOKEN` the container stops with an error. `docker/smoke.sh [image]` renders the fixture rooftop SLD to DXF, PDF, DWG and PNG inside the container, then starts the HTTP server and checks `/healthz` and the `401` for a missing token.
