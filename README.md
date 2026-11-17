# UniCAD

An MCP server that produces Indian (IS/CEA) solar EPC drawing sets (single-line diagrams, array layouts, cable schedules, earthing and lightning protection) as DXF, PDF and DWG. An agent states engineering intent through tools; a typed project model holds the design and the drawings are rendered from it with LibreCAD.

## Install

OpenCAD installs UniCAD for you: it asks once, the first time a feature needs it, and runs it as a verified MCP server. It reads [opencad-mcp.json](opencad-mcp.json), installs the released wheel with `uv tool install`, and starts `unicad-mcp` with OpenCAD's projects folder as the workspace, with OpenCAD itself as the renderer (`LIBRECAD` and `QT_QPA_PLATFORM=offscreen`, so no X display is needed).

By hand, with [uv](https://docs.astral.sh/uv/) (it fetches Python itself):

```sh
uv tool install --python 3.13 https://github.com/FuturixAI-and-Quantum-Works/UniCAD/releases/download/v0.1.2/unicad-0.1.2-py3-none-any.whl
unicad-mcp --help
```

A release is a tag `vX.Y.Z` whose version matches `pyproject.toml` and `opencad-mcp.json`; the release workflow builds the wheel and publishes it with the manifest.

## Run it

```sh
uvx --from . unicad-mcp --help                 # stdio server, for Claude, Cursor, OpenCode
UNICAD_TOKEN=change-me uvx --from . unicad-mcp --http --port 8765   # Streamable HTTP at /mcp
docker build -t unicad-mcp . && docker run -e UNICAD_TOKEN=change-me -p 8765:8765 -v unicad-data:/data unicad-mcp
```

Client configuration, environment variables and the Docker image: [docs/install.md](docs/install.md).

## Use it

- [docs/agent-workflow.md](docs/agent-workflow.md): the end-to-end tool sequence, the prompts, and a worked session run against the real tools.
- [docs/mcp-tools.md](docs/mcp-tools.md): every tool, resource and prompt.
- [docs/rules.md](docs/rules.md): the engineering rules `validate` runs.
- [docs/compat.md](docs/compat.md): what LibreCAD prints, and the DXF subset the writer is limited to.
- [PLAN.md](PLAN.md): the design.

## Develop

```sh
export LIBRECAD=~/.local/opt/librecad/squashfs-root/AppRun LIBREDWG_BIN=~/.local/opt/libredwg/bin
uv sync && uv run pytest -q
uv run unicad-mcp --write-docs docs/mcp-tools.md
uv run unicad-mcp --write-workflow docs/agent-workflow.md
```

A tool module is a file in `src/unicad/mcp/tools/` with a `register(server, workspace)` function; it is picked up without editing a registry.
