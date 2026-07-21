#!/usr/bin/env python3
"""Semi-automated smoke driver (docs/SMOKE.md, docs/M2_PLAN.md SM-02).

Connects to the real `paraview-mcp` server exactly like a real MCP client
would (stdio, mcp.ClientSession) and runs one of SMOKE.md's tool-call
sequences, printing PASS/FAIL per step. This script only automates the
MCP-client side -- SMOKE.md's numbered steps say when a human needs to
start ParaView, register/paste the bridge macro, connect to pvserver,
close a view, etc. It does not touch the ParaView GUI at all.

Usage:
    uv run python tests/smoke/run_smoke.py --scenario basic
    uv run python tests/smoke/run_smoke.py --scenario timeout
    uv run python tests/smoke/run_smoke.py --scenario disconnected
"""
import argparse
import asyncio
import base64
import os
import shutil
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

RESULTS = []


def record(name, passed, detail=""):
    RESULTS.append((name, bool(passed)))
    status = "PASS" if passed else "FAIL"
    line = "[%s] %s" % (status, name)
    if detail:
        line += " -- %s" % detail
    print(line)


def _server_command():
    # The installed console script (README.md's own MCP client config
    # uses this), not a `python -m` invocation, so this exercises exactly
    # what a real user's mcpServers entry runs.
    found = shutil.which("paraview-mcp")
    if found is None:
        print("error: 'paraview-mcp' not found on PATH -- run via `uv run python "
              "tests/smoke/run_smoke.py ...` so the project's venv is active", file=sys.stderr)
        sys.exit(2)
    return found


async def scenario_basic(session, screenshot_path):
    result = await session.call_tool("bridge_status", {})
    value = result.structuredContent or {}
    record("bridge_status: connected", not result.isError and value.get("connected") is True,
           str(value))
    if not value.get("connected"):
        record("basic scenario aborted", False,
               "bridge not reachable -- see SMOKE.md scenario 1 step 2")
        return

    result = await session.call_tool("execute_python", {
        "code": "[Delete(p) for p in list(GetSources().values())]\n"
                "Sphere()\nShow()\nUpdatePipeline()\n"
                "GetActiveSource().GetDataInformation().GetNumberOfPoints()",
        "render": True,
    })
    value = result.structuredContent or {}
    record("execute_python: Sphere point count == 50",
           not result.isError and value.get("ok") and value.get("value") == 50, str(value.get("value")))

    result = await session.call_tool("get_screenshot", {"max_width": 1280})
    image_block = next((b for b in result.content if b.type == "image"), None) if not result.isError else None
    if image_block is not None:
        screenshot_path.write_bytes(base64.b64decode(image_block.data))
    record("get_screenshot: saved a JPEG", image_block is not None, str(screenshot_path))
    if image_block is not None:
        print("  -> 目視確認してください: 球が正しく描画されているか (%s)" % screenshot_path)

    result = await session.call_tool("get_state", {"detail": "summary"})
    value = result.structuredContent or {}
    names = {s["name"] for s in (value.get("state") or {}).get("sources", [])}
    record("get_state(summary): reports Sphere1", value.get("ok") and "Sphere1" in names, str(names))

    result = await session.call_tool("get_state", {"detail": "arrays"})
    value = result.structuredContent or {}
    array_names = {a["name"] for a in (value.get("arrays") or {}).get("Sphere1", {}).get("point_arrays", [])}
    record("get_state(arrays): Sphere1 has Normals", value.get("ok") and "Normals" in array_names, str(array_names))

    result = await session.call_tool("get_state", {"detail": "full"})
    value = result.structuredContent or {}
    sphere_full = (value.get("full") or {}).get("Sphere1", {})
    record("get_state(full): bounds/points/properties present", value.get("ok") and
           len(sphere_full.get("bounds", [])) == 6 and sphere_full.get("n_points") == 50 and
           "Radius" in sphere_full.get("properties", {}), str(sphere_full)[:200])

    result = await session.call_tool("reset_session", {})
    value = result.structuredContent or {}
    record("reset_session: ok and pipeline cleared", value.get("ok") and value.get("deleted_sources"),
           str(value))

    result = await session.call_tool("get_state", {"detail": "summary"})
    value = result.structuredContent or {}
    record("get_state(summary): pipeline empty after reset",
           value.get("ok") and (value.get("state") or {}).get("sources") == [], str(value.get("state")))


async def scenario_timeout(session):
    result = await session.call_tool("execute_python", {"code": "import time; time.sleep(5)", "timeout_s": 2})
    value = result.structuredContent or {}
    message = (value.get("error") or {}).get("message", "")
    record("execute_python: times out with get_state guidance",
           value.get("ok") is False and "get_state" in message, message)

    # The bridge is still finishing the sleep (it cannot cancel in-flight
    # exec, DESIGN.md 5.4) -- this call blocks until that's done.
    result2 = await session.call_tool("execute_python", {"code": "1 + 1"})
    value2 = result2.structuredContent or {}
    record("execute_python: recovers once the slow call finishes", value2.get("value") == 2, str(value2))


async def scenario_disconnected(session):
    result = await session.call_tool("bridge_status", {})
    value = result.structuredContent or {}
    record("bridge_status: reports disconnected with macro guidance",
           value.get("connected") is False and "guidance" in value, str(value))


SCENARIOS = {"basic": scenario_basic, "timeout": scenario_timeout, "disconnected": scenario_disconnected}


async def main_async(scenario_name, screenshot_path):
    # stdio_client does NOT inherit the parent shell's environment by
    # default (the MCP SDK deliberately starts from a minimal safe env,
    # since real MCP hosts like Claude Desktop don't launch servers from
    # a shell either) -- pass it through explicitly so PARAVIEW_MCP_PORT
    # etc. set by whoever runs this script actually reach the server.
    params = StdioServerParameters(command=_server_command(), args=[], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if scenario_name == "basic":
                await scenario_basic(session, screenshot_path)
            else:
                await SCENARIOS[scenario_name](session)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--screenshot-out", default=None,
                         help="where to save the basic scenario's screenshot "
                              "(default: tests/smoke/screenshot.jpg)")
    args = parser.parse_args()

    screenshot_path = Path(args.screenshot_out) if args.screenshot_out else Path(__file__).parent / "screenshot.jpg"

    asyncio.run(main_async(args.scenario, screenshot_path))

    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print("\n%d/%d PASS" % (passed, total))
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
