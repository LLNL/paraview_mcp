# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ParaView MCP Server — bridges ParaView's visualization capabilities to LLMs via the Model Context Protocol. The server connects to a running `pvserver` instance over the network and exposes ParaView operations as MCP tools.

## Architecture

Three-layer client-server model:

1. **pvserver** — ParaView's server process, runs on `localhost:11111` with `--multi-clients`
2. **MCP Server** (this project) — connects to pvserver via `paraview.simple.Connect()`, exposes tools via FastMCP
3. **Claude Code / Claude Desktop** — consumes MCP tools

Two core files:
- **`paraview_mcp_server.py`** — FastMCP tool definitions (thin wrappers that delegate to the manager)
- **`paraview_manager.py`** — `ParaViewManager` class encapsulating all `paraview.simple` API interactions

Launch infrastructure:
- **`run_server.sh`** — sets `DYLD_FALLBACK_LIBRARY_PATH` for VTK dylibs, runs via venv Python
- **`run_server.py`** — appends ParaView's Python path *after* venv packages (avoids shadowing pydantic's typing_extensions with ParaView's older version)

## Adding New Tools

Follow the existing pattern:

1. Add a method to `ParaViewManager` in `paraview_manager.py` that returns a tuple `(success: bool, message: str, ...)`. Import from `paraview.simple` inside the method body.
2. Add a `@mcp.tool()` function in `paraview_mcp_server.py` that calls the manager method and returns the message string.
3. Add the tool to the `list_commands()` output.

For dict/list parameters passed from the LLM, use typed signatures like `list[dict[str, float]]` and transform to the internal format in the MCP wrapper (see `edit_volume_opacity` and `set_color_map` for examples).

## Running

```bash
# Terminal 1: Start pvserver
/Applications/ParaView-6.0.1.app/Contents/bin/pvserver --multi-clients

# ParaView GUI: File > Connect > localhost:11111

# Terminal 2 (or via Claude Code MCP config): Start MCP server
/Users/tomer/Documents/paraview_mcp/run_server.sh
```

Register with Claude Code:
```bash
claude mcp add-json ParaView '{"command":"/Users/tomer/Documents/paraview_mcp/run_server.sh","args":[]}' --scope user
```

## Key Design Decisions

- **`original_source`**: The manager stores the first loaded data source separately. Volume rendering and some filters reference this to ensure they operate on the root data, not a derived filter.
- **`_data_folder`**: Tracks the directory of loaded data so `save_contour_as_stl` exports to the same location.
- **RAW file handling**: Parses dimensions and data type from the filename (e.g., `foot_256x256x256_uint8.raw`).
- **ParaView 6.0.1 API**: Uses `Invert` (not the deprecated `InsideOut`) on the Clip filter.

## Logging

Logs to `~/paraview_logs/paraview_mcp_external.log` (INFO level, file + console).

## Git

Branch: `local-setup` (forked from LLNL/paraview_mcp). Uses conventional commits.
