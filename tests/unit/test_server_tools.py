"""Server tool tests: S-01, S-03 through S-06. bridge_client replaced with
a scriptable fake double -- no real bridge, no real socket.
"""
import base64
import io
import json

from mcp.server.fastmcp import Image
from PIL import Image as PILImage

from paraview_mcp import bridge_client, server, snippets


class FakeBridgeClientDouble:
    """Records calls and returns/raises whatever the test scripted, in
    place of a real bridge_client.BridgeClient."""

    def __init__(self):
        self.host = "127.0.0.1"
        self.port = 9911
        self.exec_calls = []
        self.call_calls = []
        self.exec_response = None
        self.call_response = None

    async def exec(self, code, render=True, timeout_s=120, max_value_bytes=None):
        self.exec_calls.append({"code": code, "render": render, "timeout_s": timeout_s,
                                 "max_value_bytes": max_value_bytes})
        if isinstance(self.exec_response, Exception):
            raise self.exec_response
        return self.exec_response

    async def call(self, op, timeout_s, **fields):
        self.call_calls.append({"op": op, "timeout_s": timeout_s, "fields": fields})
        if isinstance(self.call_response, Exception):
            raise self.call_response
        return self.call_response


def install_fake(monkeypatch):
    fake = FakeBridgeClientDouble()
    monkeypatch.setattr(server, "_bridge", fake)
    return fake


# ---- S-03: execute_python ---------------------------------------------------

async def test_execute_python_propagates_arguments_and_maps_response(monkeypatch):
    fake = install_fake(monkeypatch)
    fake.exec_response = {
        "status": "ok", "value": "2", "value_is_json": True,
        "stdout": "hi", "stderr": "", "vtk_messages": "",
        "state": {"sources": []}, "duration_ms": 5,
    }
    result = await server.execute_python("1+1", timeout_s=30, render=False)
    assert fake.exec_calls[0]["code"] == "1+1"
    assert fake.exec_calls[0]["timeout_s"] == 30
    assert fake.exec_calls[0]["render"] is False
    assert result["ok"] is True
    assert result["value"] == 2  # unwrapped from the wire's string encoding
    assert result["stdout"] == "hi"
    assert result["state"] == {"sources": []}


async def test_execute_python_error_includes_traceback_and_vtk_messages(monkeypatch):
    fake = install_fake(monkeypatch)
    fake.exec_response = {
        "status": "error",
        "error": {"kind": "exec_error", "type": "ZeroDivisionError",
                   "message": "division by zero", "traceback": "Traceback...\n"},
        "stdout": "", "stderr": "", "vtk_messages": "vtk error here",
        "state": None, "duration_ms": 1,
    }
    result = await server.execute_python("1/0")
    assert result["ok"] is False
    assert result["error"]["traceback"] == "Traceback...\n"
    assert result["vtk_messages"] == "vtk error here"


async def test_execute_python_bridge_error_becomes_structured_result_not_raise(monkeypatch):
    fake = install_fake(monkeypatch)
    fake.exec_response = bridge_client.BridgeUnavailableError("run the macro to start it")
    result = await server.execute_python("1+1")
    assert result["ok"] is False
    assert "run the macro" in result["error"]["message"]


# ---- S-04: get_screenshot ----------------------------------------------------

def _fake_png_response(width=2000, height=1000, view_type="RenderView"):
    img = PILImage.new("RGB", (width, height), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    value = json.dumps({"view_type": view_type, "png_base64": png_b64})
    return {"status": "ok", "value": value, "value_is_json": True,
            "stdout": "", "stderr": "", "vtk_messages": "", "state": None, "duration_ms": 5}


async def test_get_screenshot_returns_resized_jpeg_and_descriptive_text(monkeypatch):
    fake = install_fake(monkeypatch)
    fake.exec_response = _fake_png_response(width=2000, height=1000)

    result = await server.get_screenshot(max_width=800, quality=70)

    assert fake.exec_calls[0]["max_value_bytes"] == snippets.GET_SCREENSHOT_MAX_VALUE_BYTES
    assert fake.exec_calls[0]["code"] == snippets.GET_SCREENSHOT

    image, text = result
    assert isinstance(image, Image)
    out_img = PILImage.open(io.BytesIO(image.data))
    assert out_img.format == "JPEG"
    assert out_img.width <= 800
    assert "RenderView" in text
    assert "2000x1000" in text


async def test_get_screenshot_leaves_small_images_unresized(monkeypatch):
    fake = install_fake(monkeypatch)
    fake.exec_response = _fake_png_response(width=400, height=300)
    result = await server.get_screenshot(max_width=800)
    image, text = result
    out_img = PILImage.open(io.BytesIO(image.data))
    assert out_img.width == 400
    assert out_img.height == 300


async def test_get_screenshot_bridge_error_returns_text_not_raise(monkeypatch):
    fake = install_fake(monkeypatch)
    fake.exec_response = bridge_client.BridgeTimeoutError("timed out")
    result = await server.get_screenshot()
    assert result == ["timed out"]


# ---- S-05: bridge_status -----------------------------------------------------

async def test_bridge_status_success_maps_ping_value_and_server_config(monkeypatch):
    fake = install_fake(monkeypatch)
    fake.call_response = {"status": "ok", "value": {
        "bridge_version": "1.0.0", "paraview_version": "6.1.1",
        "python_version": "3.9.7", "session_type": "builtin", "server": None,
    }}
    result = await server.bridge_status()
    assert result["connected"] is True
    assert result["bridge_version"] == "1.0.0"
    assert result["server_host"] == fake.host
    assert result["server_port"] == fake.port


async def test_bridge_status_unreachable_returns_guidance_not_exception(monkeypatch):
    fake = install_fake(monkeypatch)
    fake.call_response = bridge_client.BridgeUnavailableError(
        "run the paraview_mcp_bridge macro to start it")
    result = await server.bridge_status()
    assert result["connected"] is False
    assert "macro" in result["guidance"]


# ---- S-01 / S-06 --------------------------------------------------------------

def test_logging_never_writes_to_stdout(capfd):
    server.logger.info("hello-info-marker")
    server.logger.warning("hello-warn-marker")
    out, err = capfd.readouterr()
    assert "hello-info-marker" not in out
    assert "hello-warn-marker" not in out


def test_instructions_are_not_empty():
    assert server.INSTRUCTIONS.strip()
    assert len(server.INSTRUCTIONS) > 100
