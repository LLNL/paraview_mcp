# ParaView MCP - Local Setup

Local setup for [paraview_mcp](https://github.com/LLNL/paraview_mcp) on this machine.
This diverges from the upstream README (which uses conda) because we already have
ParaView 6.0.1 installed as a macOS app and don't have conda.

## How it works

The upstream approach installs ParaView via conda into a single Python environment
alongside the MCP dependencies. We can't do that because:

- No conda installed
- ParaView 6.0.1 is already installed at `/Applications/ParaView-6.0.1.app`
- ParaView's bundled `pvpython` is too stripped-down (no ssl, no pip, no tomllib)
  to run the MCP server directly

Instead, we use a standalone Python 3.12 venv for the MCP dependencies and load
ParaView's Python packages from the .app bundle at runtime.

### The path-ordering problem

ParaView ships its own `typing_extensions.py` which is older than what `mcp` requires.
If ParaView's Python path is *prepended* (via `PYTHONPATH`), it shadows the venv's version
and breaks pydantic. The fix: *append* ParaView's path (via `sys.path.append` in
`run_server.py`) so the venv's packages take precedence.

### The native library problem

ParaView's `.so` modules use `@executable_path/../Libraries` to find VTK dylibs.
When running from the venv's Python (not pvpython), that path resolves wrong.
The fix: set `DYLD_FALLBACK_LIBRARY_PATH` to ParaView's Libraries directory.
Using `_FALLBACK_` (not `DYLD_LIBRARY_PATH`) avoids interfering with the venv
Python's own standard library C extensions.

## What was installed

### Python 3.12 (via uv)

```
uv python install 3.12
```

### Venv + dependencies

```
uv venv --python 3.12 /Users/tomer/Documents/paraview_mcp/.venv
uv pip install --python .venv/bin/python 'mcp[cli]' httpx Pillow
```

### Compatibility fix

`paraview_mcp_server.py` line 57: changed `system_prompt=` to `instructions=`
for mcp v1.26+ compatibility (upstream was written against an older mcp API).

### Files added

- `run_server.py` - Python wrapper that appends ParaView's package path to `sys.path`
  then imports and runs the MCP server
- `run_server.sh` - Shell launcher that sets `DYLD_FALLBACK_LIBRARY_PATH` and
  execs `run_server.py` via the venv Python
- `.venv/` - Python 3.12 virtual environment with MCP dependencies

### Claude Code MCP registration

```
claude mcp add-json ParaView '{"command":"/Users/tomer/Documents/paraview_mcp/run_server.sh","args":[]}' --scope user
```

## Usage

### 1. Start pvserver

```
pvs
```

This is an alias (defined in `~/.zshrc`) for:
```
/Applications/ParaView-6.0.1.app/Contents/bin/pvserver --multi-clients
```

Leave this running in a terminal.

### 2. Connect the ParaView GUI

- Open ParaView
- File -> Connect
- Connect to `localhost:11111` (the default)

### 3. Start a Claude Code session

The ParaView MCP server is registered at user scope and will be available in any
new Claude Code session. The MCP server connects to pvserver automatically on startup.

### Available MCP tools

Once connected, Claude has access to these ParaView tools:

- `load_data` - Load VTK, EXODUS, CSV, RAW files
- `create_source` - Create primitives (Sphere, Cone, Cylinder, Plane, Box)
- `create_isosurface` - Isosurface at specified values
- `create_slice` - Planar slices through volumes
- `create_streamline` - Streamlines from vector fields
- `warp_by_vector` - Deform geometry by vector field
- `toggle_volume_rendering` - Enable/disable volume rendering
- `toggle_visibility` - Show/hide sources
- `set_representation_type` - Surface, Wireframe, Points
- `color_by` - Color by data field
- `set_color_map` - Custom color transfer functions
- `edit_volume_opacity` - Opacity transfer functions
- `set_active_source` - Select pipeline objects by name
- `get_active_source_names_by_type` - Filter sources by type
- `get_pipeline` - Display pipeline structure
- `get_available_arrays` - List data arrays
- `compute_surface_area` - Calculate mesh surface area
- `plot_over_line` - Sample data along a line
- `get_screenshot` - Capture viewport image (visual feedback loop)
- `rotate_camera` / `reset_camera` - Adjust view
- `save_contour_as_stl` - Export surfaces as STL
