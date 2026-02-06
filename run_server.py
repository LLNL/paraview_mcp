"""Wrapper that appends ParaView's Python path (so it doesn't shadow venv packages)
then runs the MCP server."""
import sys
sys.path.append("/Applications/ParaView-6.0.1.app/Contents/Python")

# Now import and run the server's main
from paraview_mcp_server import main
main()
