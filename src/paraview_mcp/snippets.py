"""Canned exec() code strings sent to the bridge for tool-level behavior.

DESIGN.md section 3 principle 1: the bridge is a dumb executor with only
ping/exec/reset. Anything beyond that -- today just get_screenshot -- is a
plain code string run through the same `exec` op a user's own code would
use. New server-side tools belong here, never in bridge/paraview_mcp_bridge.py
(frozen for the v1 wire protocol lifetime, docs/M1_PLAN.md 1.1).

Double-underscore-prefixed local names avoid colliding with whatever the
user's own persistent namespace (DESIGN.md 6.1) happens to contain --
these snippets share that namespace, not a private one.
"""

GET_SCREENSHOT = """\
import base64 as __b64, os as __os, tempfile as __tempfile
__view = simple.GetActiveView()
if __view is None or not hasattr(__view, "GetInteractor"):
    __views = simple.GetRenderViews()
    __view = __views[0] if __views else simple.CreateRenderView()
__view_type = __view.GetXMLName() if hasattr(__view, "GetXMLName") else type(__view).__name__
__fd, __path = __tempfile.mkstemp(suffix=".png")
__os.close(__fd)
try:
    simple.SaveScreenshot(__path, __view)
    with open(__path, "rb") as __f:
        __data = __f.read()
finally:
    try:
        __os.remove(__path)
    except OSError:
        pass
{"view_type": __view_type, "png_base64": __b64.b64encode(__data).decode("ascii")}
"""

# get_screenshot's snippet response can be a multi-MB base64 PNG; the
# default 256 KiB `value` truncation (DESIGN.md 6.2) would silently
# corrupt it, so the server must raise max_value_bytes when sending this
# snippet (DESIGN.md 5.2 / 7.2).
GET_SCREENSHOT_MAX_VALUE_BYTES = 32 * 1024 * 1024
