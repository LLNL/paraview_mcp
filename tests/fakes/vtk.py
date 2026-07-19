"""Fake `vtk` module: just vtkOutputWindow/vtkStringOutputWindow, the only
surface bridge/paraview_mcp_bridge.py touches (docs/M1_PLAN.md 4.1 seam 1).
"""


class _GuiOutputWindow:
    """Sentinel standing in for ParaView's own (non-string) output window,
    i.e. whatever vtkOutputWindow.GetInstance() would return before the
    bridge substitutes its own vtkStringOutputWindow."""


class vtkOutputWindow:
    @staticmethod
    def GetInstance():
        return _current_instance

    @staticmethod
    def SetInstance(instance):
        global _current_instance
        _current_instance = instance


class vtkStringOutputWindow:
    def __init__(self):
        self._buf = []

    def GetOutput(self):
        return "".join(self._buf)

    def _write(self, message):
        self._buf.append(message)


_current_instance = _GuiOutputWindow()


def _reset():
    global _current_instance
    _current_instance = _GuiOutputWindow()


def fake_vtk_error(message):
    """Test helper: simulate a vtkErrorMacro firing through whatever
    vtkOutputWindow instance is current, exactly as real VTK C++ code
    would -- only lands in the output if a vtkStringOutputWindow is
    currently installed (i.e. the bridge is mid-exec)."""
    inst = vtkOutputWindow.GetInstance()
    if isinstance(inst, vtkStringOutputWindow):
        inst._write(message)
