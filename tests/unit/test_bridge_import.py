"""B-01: the bridge must be safely importable with no paraview/vtk
installed at all, and must not bind or start anything at import time."""
import sys

from tests.unit.conftest import load_bridge_module


def test_import_succeeds_without_any_paraview(bridge_no_paraview):
    assert bridge_no_paraview is not None


def test_import_does_not_touch_paraview_or_vtk_in_sys_modules(bridge_no_paraview):
    assert "paraview" not in sys.modules
    assert "vtk" not in sys.modules


def test_import_does_not_start_a_listener(bridge_no_paraview):
    assert bridge_no_paraview._listener is None


def test_reimport_is_isolated_from_prior_module(bridge_no_paraview):
    other = load_bridge_module()
    other._listener = object()
    assert bridge_no_paraview._listener is None
