"""paraview_mcp MCP server: execute_python / get_screenshot / bridge_status
over stdio (DESIGN.md section 7). Talks to the bridge only through
bridge_client -- never imports paraview/vtk directly (DESIGN.md 4.2).
"""
import base64
import io
import json
import logging
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage

from paraview_mcp import snippets
from paraview_mcp.bridge_client import BridgeClient, BridgeError

INSTRUCTIONS = """\
This server controls a running ParaView GUI (or a headless pvpython
instance) by sending Python code strings to execute inside it. Guidelines:

- Prefer several small execute_python calls over one large script. Each
  response's `state` field already summarizes the current pipeline
  (sources, visibility, active source) -- check it before deciding what
  to do next instead of guessing or re-querying.
- After a call that changes what should be visible, call get_screenshot
  to see the actual result before proceeding.
- To remove a proxy: `Delete(obj); del obj` (both steps -- ParaView keeps
  a proxy alive as long as a Python reference to it exists).
- Heavy operations (large datasets, expensive filters) block the ParaView
  GUI until they finish -- this is expected, not a bug. Warn the user
  before running one ("ParaView will look frozen while this runs") and
  pass a larger timeout_s.
- If an execute_python call fails, read both `error` (type/message/
  traceback) and `vtk_messages` -- VTK errors often don't raise Python
  exceptions.
- If `value` comes back truncated, narrow the code to return only the
  specific number or short summary actually needed, not a whole object.
- File paths in code refer to paths on the machine running ParaView, not
  the machine running this MCP server -- they can differ (e.g. WSL vs.
  Windows).
- Never call input(), open a dialog, or call exit()/quit(): ParaView has
  no human at the keyboard to answer, and exit() would kill the GUI.
- get_state(detail="summary") is cheap (no server round trip) -- prefer it
  over guessing from execute_python's own `state` field when you just need
  a fresh look. Use "arrays" or "full" only when you need array
  names/ranges or bounds/properties, since those do a real round trip per
  source.
- reset_session deletes the whole pipeline and/or clears the persistent
  namespace -- use it to start over, not to remove a single source (use
  `Delete(obj); del obj` for that, as above).
"""


def _configure_logging():
    handlers = [logging.StreamHandler(sys.stderr)]
    log_path = os.environ.get("PARAVIEW_MCP_LOG")
    if log_path:
        handlers.append(logging.FileHandler(log_path))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                         format="%(asctime)s %(levelname)s %(name)s: %(message)s")


_configure_logging()
logger = logging.getLogger("paraview_mcp")

mcp = FastMCP("paraview-mcp", instructions=INSTRUCTIONS)
_bridge = BridgeClient()


def _decode_value(resp):
    """Undo the wire protocol's always-a-string `value` encoding
    (DESIGN.md 6.2) so the LLM gets the real type back, not a
    double-encoded string."""
    value = resp.get("value")
    if value is None:
        return None
    if resp.get("value_is_json"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value  # truncated mid-value; hand back the raw text
    return value


@mcp.tool()
async def execute_python(code: str, timeout_s: int = 120, render: bool = True) -> dict[str, Any]:
    """Execute a Python code string inside the running ParaView session.

    paraview.simple is already imported (as `simple` and star-imported).
    The namespace persists across calls. The value of the last expression
    (if the code ends in one) is returned as `value`, IPython-style.
    """
    try:
        resp = await _bridge.exec(code, render=render, timeout_s=timeout_s)
    except BridgeError as e:
        return {"ok": False, "value": None, "stdout": "", "stderr": "", "vtk_messages": "",
                "state": None, "duration_ms": None,
                "error": {"kind": "connection_error", "message": str(e)}}

    result = {
        "ok": resp.get("status") == "ok",
        "value": _decode_value(resp),
        "stdout": resp.get("stdout", ""),
        "stderr": resp.get("stderr", ""),
        "vtk_messages": resp.get("vtk_messages", ""),
        "state": resp.get("state"),
        "duration_ms": resp.get("duration_ms"),
    }
    if resp.get("status") == "error":
        result["error"] = resp.get("error")
    return result


@mcp.tool()
async def get_screenshot(max_width: int = 1280, quality: int = 80):
    """Capture the active (or first) RenderView and return it as a JPEG
    image, resized to max_width if wider."""
    try:
        resp = await _bridge.exec(snippets.GET_SCREENSHOT, render=False, timeout_s=60,
                                   max_value_bytes=snippets.GET_SCREENSHOT_MAX_VALUE_BYTES)
    except BridgeError as e:
        return [str(e)]

    if resp.get("status") == "error":
        error = resp.get("error") or {}
        return ["get_screenshot failed: %s: %s" % (error.get("type"), error.get("message"))]

    payload = _decode_value(resp)
    if not isinstance(payload, dict) or "png_base64" not in payload:
        return ["get_screenshot: unexpected snippet response shape: %r" % (payload,)]

    raw_png = base64.b64decode(payload["png_base64"])
    img = PILImage.open(io.BytesIO(raw_png))
    img.load()
    orig_width, orig_height = img.size
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, max(1, round(img.height * ratio))))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    jpeg_bytes = buf.getvalue()

    text = ("view=%s original=%dx%d resized=%dx%d bytes=%d"
            % (payload.get("view_type"), orig_width, orig_height,
               img.width, img.height, len(jpeg_bytes)))
    return [Image(data=jpeg_bytes, format="jpeg"), text]


@mcp.tool()
async def bridge_status() -> dict[str, Any]:
    """Check whether the ParaView bridge is reachable and report its
    version/session info. Never raises -- connection failures come back
    as a normal tool result with guidance, not an exception."""
    result = {"server_host": _bridge.host, "server_port": _bridge.port}
    try:
        resp = await _bridge.call("ping", timeout_s=10)
    except BridgeError as e:
        result["connected"] = False
        result["guidance"] = str(e)
        return result
    result["connected"] = True
    result.update(resp.get("value") or {})
    return result


_DETAIL_SNIPPETS = {"arrays": snippets.GET_STATE_ARRAYS, "full": snippets.GET_STATE_FULL}


@mcp.tool()
async def get_state(detail: str = "summary") -> dict[str, Any]:
    """Get a summary of the current ParaView pipeline: sources (name,
    type, visible, active), the active view, and animation time.

    detail="summary" (default) is served from the lightweight state
    snapshot every bridge response already carries -- no extra work in
    ParaView. detail="arrays" adds each source's point/cell array names,
    component counts, and value ranges (one real round trip per source).
    detail="full" adds bounds, point/cell counts, and scalar property
    values on top of arrays.
    """
    if detail not in ("summary", "arrays", "full"):
        return {"ok": False, "state": None,
                "error": {"kind": "invalid_argument",
                          "message": "detail must be one of: summary, arrays, full (got %r)" % detail}}

    if detail == "summary":
        try:
            resp = await _bridge.call("ping", timeout_s=10)
        except BridgeError as e:
            return {"ok": False, "state": None, "error": {"kind": "connection_error", "message": str(e)}}
        return {"ok": True, "detail": "summary", "state": resp.get("state")}

    try:
        resp = await _bridge.exec(_DETAIL_SNIPPETS[detail], render=False, timeout_s=60,
                                   max_value_bytes=snippets.GET_STATE_MAX_VALUE_BYTES)
    except BridgeError as e:
        return {"ok": False, "state": None, "error": {"kind": "connection_error", "message": str(e)}}
    if resp.get("status") == "error":
        error = resp.get("error") or {}
        return {"ok": False, "state": resp.get("state"),
                "error": {"kind": "exec_error", "message": "%s: %s" % (error.get("type"), error.get("message"))}}

    result = {"ok": True, "detail": detail, "state": resp.get("state")}
    result.update(_decode_value(resp) or {})
    return result


@mcp.tool()
async def reset_session(clear_pipeline: bool = True, clear_namespace: bool = True) -> dict[str, Any]:
    """Reset the ParaView session: delete every source in the pipeline
    (clear_pipeline) and/or clear the persistent namespace execute_python
    code runs in, back to its just-imported state (clear_namespace).

    Use this to start over, not to remove a single source -- for that,
    use `Delete(obj); del obj` inside execute_python instead.
    """
    result = {"ok": True, "deleted_sources": [], "namespace_cleared": False, "state": None}

    if clear_pipeline:
        try:
            resp = await _bridge.exec(snippets.RESET_PIPELINE, render=False, timeout_s=60)
        except BridgeError as e:
            return {"ok": False, "error": {"kind": "connection_error", "message": str(e)}}
        if resp.get("status") == "error":
            error = resp.get("error") or {}
            return {"ok": False, "state": resp.get("state"),
                    "error": {"kind": "exec_error",
                              "message": "%s: %s" % (error.get("type"), error.get("message"))}}
        result["deleted_sources"] = (_decode_value(resp) or {}).get("deleted", [])
        result["state"] = resp.get("state")

    if clear_namespace:
        try:
            resp = await _bridge.reset(timeout_s=30)
        except BridgeError as e:
            return {"ok": False, "error": {"kind": "connection_error", "message": str(e)}}
        if resp.get("status") == "error":
            error = resp.get("error") or {}
            return {"ok": False, "state": resp.get("state"),
                    "error": {"kind": "exec_error",
                              "message": "%s: %s" % (error.get("type"), error.get("message"))}}
        result["namespace_cleared"] = True
        result["state"] = resp.get("state")

    return result


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
