"""Full-chain integration tests (docs/M2_PLAN.md I-04): a real MCP client
talks stdio to the real `paraview_mcp.server`, which talks TCP to a real
`pvpython --standalone` bridge. Nothing is mocked or faked anywhere in
this path -- the strongest automated check the test suite has that the
whole system actually works end to end.

Scope: docs/M2_PLAN.md I-05 (protocol/exec semantics/offscreen rendering
only -- not the GUI/timer/Qt path, not pixel-level screenshot content).
"""
import base64
import io
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from PIL import Image


def _server_params(bridge):
    return StdioServerParameters(
        command=sys.executable,
        args=["-c", "from paraview_mcp.server import main; main()"],
        env={"PARAVIEW_MCP_HOST": bridge.host, "PARAVIEW_MCP_PORT": str(bridge.port)},
    )


async def _call(bridge, tool, arguments=None):
    """One-shot MCP client: connect, call a single tool, disconnect. The
    server reconnects to the bridge on demand (DESIGN.md 4.2), so a fresh
    client per call is fine -- it also means each call exercises the
    server's own startup path, not just a long-lived warm connection.
    """
    async with stdio_client(_server_params(bridge)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool, arguments or {})


def _structured(result):
    assert not result.isError, result.content
    return result.structuredContent


async def test_bridge_status_reports_real_paraview(standalone_bridge):
    result = await _call(standalone_bridge, "bridge_status")
    value = _structured(result)
    assert value["connected"] is True
    assert value["session_type"] == "builtin"
    assert value["paraview_version"]


async def test_execute_python_creates_sphere_and_reports_point_count(standalone_bridge):
    result = await _call(standalone_bridge, "execute_python", {
        "code": "[Delete(p) for p in list(GetSources().values())]\n"
                "Sphere()\nUpdatePipeline()\nGetActiveSource().GetDataInformation().GetNumberOfPoints()",
        "render": False,
    })
    value = _structured(result)
    assert value["ok"] is True
    assert value["value"] == 50  # ParaView's default Sphere() point count
    names = {s["name"] for s in value["state"]["sources"]}
    assert "Sphere1" in names


async def test_get_screenshot_returns_valid_jpeg_of_expected_size(standalone_bridge):
    result = await _call(standalone_bridge, "get_screenshot", {"max_width": 400})
    assert not result.isError, result.content
    image_block = next(b for b in result.content if b.type == "image")
    assert image_block.mimeType == "image/jpeg"
    raw = base64.b64decode(image_block.data)
    assert raw[:2] == b"\xff\xd8"  # JPEG SOI marker -- "valid JPEG", not pixel comparison (DESIGN.md 11-2)
    img = Image.open(io.BytesIO(raw))
    img.load()
    assert img.width <= 400


async def test_get_state_arrays_matches_execute_python_pipeline(standalone_bridge):
    await _call(standalone_bridge, "execute_python", {
        "code": "[Delete(p) for p in list(GetSources().values())]\nSphere()", "render": False,
    })
    result = await _call(standalone_bridge, "get_state", {"detail": "arrays"})
    value = _structured(result)
    assert value["ok"] is True
    assert value["detail"] == "arrays"
    sphere_arrays = value["arrays"]["Sphere1"]
    names = {a["name"] for a in sphere_arrays["point_arrays"]}
    assert "Normals" in names


async def test_get_state_full_includes_bounds_and_properties(standalone_bridge):
    await _call(standalone_bridge, "execute_python", {
        "code": "[Delete(p) for p in list(GetSources().values())]\nSphere(Radius=3.0)", "render": False,
    })
    result = await _call(standalone_bridge, "get_state", {"detail": "full"})
    value = _structured(result)
    sphere = value["full"]["Sphere1"]
    assert sphere["n_points"] == 50
    assert sphere["properties"]["Radius"] == 3.0
    assert len(sphere["bounds"]) == 6


async def test_reset_session_clears_pipeline_end_to_end(standalone_bridge):
    await _call(standalone_bridge, "execute_python", {"code": "Sphere(); Cone()", "render": False})
    result = await _call(standalone_bridge, "reset_session")
    value = _structured(result)
    assert value["ok"] is True
    assert value["deleted_sources"]  # non-empty: at least Sphere/Cone just created
    assert value["namespace_cleared"] is True
    assert value["state"]["sources"] == []

    # a fresh execute_python call must start from an empty namespace too
    check = await _call(standalone_bridge, "execute_python", {"code": "'x' in dir()", "render": False})
    assert _structured(check)["value"] is False


async def test_get_state_invalid_detail_is_a_tool_result_not_a_protocol_error(standalone_bridge):
    result = await _call(standalone_bridge, "get_state", {"detail": "bogus"})
    value = _structured(result)
    assert value["ok"] is False
    assert "detail" in value["error"]["message"]
