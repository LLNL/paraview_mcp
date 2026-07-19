"""Fake `paraview` package for ParaView-free unit testing
(docs/M1_PLAN.md section 4.1, seam 1). Only the surface
bridge/paraview_mcp_bridge.py actually touches.

Relative imports only: this package is imported both in-process as
`tests.fakes.paraview` (sys.modules injection, conftest.py) and, for
test_standalone_subprocess.py, as a bare top-level `paraview` package by
pointing a subprocess's PYTHONPATH at tests/fakes/ directly. An absolute
`from tests.fakes.paraview import ...` would only work under the former.
"""
from . import (
    servermanager,  # noqa: F401
    simple,  # noqa: F401
)
