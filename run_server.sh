#!/bin/bash
# Launcher for ParaView MCP server
# Sets up the library path so ParaView's VTK modules can load from a standalone Python 3.12

export DYLD_FALLBACK_LIBRARY_PATH=/Applications/ParaView-6.0.1.app/Contents/Libraries
exec /Users/tomer/Documents/paraview_mcp/.venv/bin/python \
    /Users/tomer/Documents/paraview_mcp/run_server.py \
    "$@"
