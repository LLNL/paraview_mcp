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


# --------------------------------------------------------------------------
# get_state(detail="arrays"/"full") -- M2_PLAN.md S-08.
#
# "summary" needs neither of these: it is served straight from the `state`
# block every response already carries (B-17), via a plain `ping` call --
# no exec, no snippet. These two snippets are only for the detail levels
# that need a server round trip (GetDataInformation), which B-17
# deliberately excludes from the per-response summary.
# --------------------------------------------------------------------------

# Shared by both snippets below (each is a standalone exec() request, so it
# can't just import the other -- duplicated on purpose, not factored into a
# helper module).
_ARRAY_INFO_HELPER = """\
def __array_info(__attr_info):
    __out = []
    for __i in range(__attr_info.GetNumberOfArrays()):
        __ai = __attr_info.GetArrayInformation(__i)
        __ncomp = __ai.GetNumberOfComponents()
        __out.append({
            "name": __ai.GetName(),
            "n_components": __ncomp,
            "ranges": [list(__ai.GetComponentRange(__c)) for __c in range(__ncomp)],
        })
    return __out
"""

GET_STATE_ARRAYS = _ARRAY_INFO_HELPER + """\
__arrays = {}
for __name_id, __proxy in simple.GetSources().items():
    try:
        simple.UpdatePipeline(proxy=__proxy)
        __di = __proxy.GetDataInformation()
        __arrays[__name_id[0]] = {
            "point_arrays": __array_info(__di.GetPointDataInformation()),
            "cell_arrays": __array_info(__di.GetCellDataInformation()),
        }
    except Exception as __e:
        __arrays[__name_id[0]] = {"error": str(__e)}
{"arrays": __arrays}
"""

GET_STATE_FULL = _ARRAY_INFO_HELPER + """\
def __prop_info(__proxy):
    # getattr(proxy, prop) on a vector property (e.g. Sphere.Center) returns
    # a paraview.servermanager *Property wrapper object, not a plain list --
    # isinstance(val, (list, tuple)) is False for it even though it behaves
    # like one (iterates, indexes). list(val) normalizes both that and
    # already-plain sequences; proxy-valued properties (e.g. Clip.Input)
    # raise TypeError on list() since they're not iterable, so they're
    # skipped along with anything else that doesn't reduce to JSON scalars.
    __out = {}
    for __prop in __proxy.ListProperties():
        try:
            __val = getattr(__proxy, __prop)
        except Exception:
            continue
        if isinstance(__val, (int, float, str, bool)) or __val is None:
            __out[__prop] = __val
            continue
        try:
            __seq = list(__val)
        except TypeError:
            continue
        if all(isinstance(__v, (int, float, str, bool)) for __v in __seq):
            __out[__prop] = __seq
    return __out

__full = {}
for __name_id, __proxy in simple.GetSources().items():
    try:
        simple.UpdatePipeline(proxy=__proxy)
        __di = __proxy.GetDataInformation()
        __full[__name_id[0]] = {
            "point_arrays": __array_info(__di.GetPointDataInformation()),
            "cell_arrays": __array_info(__di.GetCellDataInformation()),
            "bounds": list(__di.GetBounds()),
            "n_points": __di.GetNumberOfPoints(),
            "n_cells": __di.GetNumberOfCells(),
            "properties": __prop_info(__proxy),
        }
    except Exception as __e:
        __full[__name_id[0]] = {"error": str(__e)}
{"full": __full}
"""

# arrays/full read every source's DataInformation (component ranges,
# property values) -- comfortably under 256 KiB for reasonable pipelines,
# but a pipeline with many sources or many-component arrays can exceed it,
# so raise the same way get_screenshot does (DESIGN.md 5.2 / 6.2). Smaller
# than GET_SCREENSHOT_MAX_VALUE_BYTES: this is text, not base64 image data.
GET_STATE_MAX_VALUE_BYTES = 4 * 1024 * 1024


# --------------------------------------------------------------------------
# reset_session(clear_pipeline=True) -- M2_PLAN.md S-09.
# --------------------------------------------------------------------------

# DESIGN.md 7.4 originally called for deleting leaf-to-root (repeatedly
# deleting whatever nothing else still references) out of caution about
# deleting a source a filter downstream still points to. Verified against
# real ParaView 6.1.1 (docs/M2_PLAN.md 1.4-3): Delete() tolerates any
# order, including deleting a source before the filter consuming it --
# no exception, and the view's Representations for both are gone
# afterward. So plain registration-order deletion is sufficient; no
# dependency bookkeeping needed.
RESET_PIPELINE = """\
__deleted = []
for __name_id, __proxy in list(simple.GetSources().items()):
    try:
        simple.Delete(__proxy)
        __deleted.append(__name_id[0])
    except Exception:
        pass
{"deleted": __deleted}
"""
