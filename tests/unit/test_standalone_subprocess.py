"""End-to-end subprocess test (B-09): launches the real bridge file as a
standalone process against fake paraview/vtk on PYTHONPATH and drives it
over a real TCP socket. This is the top-level "does the whole bridge work
as one process" unit test; the real pvpython version is M2 integration's
job (docs/M1_PLAN.md 4.2/4.3).
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.unit.conftest import free_tcp_port

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = PROJECT_ROOT / "bridge" / "paraview_mcp_bridge.py"
FAKES_PATH = PROJECT_ROOT / "tests" / "fakes"


def _launch(port):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(FAKES_PATH), str(PROJECT_ROOT), env.get("PYTHONPATH", "")]
    )
    return subprocess.Popen(
        [sys.executable, str(BRIDGE_PATH), "--standalone", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _wait_for_listening(port, proc, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError("bridge subprocess exited early:\n" + proc.stdout.read())
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    proc.kill()
    raise AssertionError("bridge subprocess never started listening on port %d" % port)


def _send(port, req, timeout=5):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        s.settimeout(timeout)
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("bridge closed the connection without a response")
            buf += chunk
    return json.loads(buf.partition(b"\n")[0].decode("utf-8"))


def test_standalone_subprocess_ping_exec_and_sigterm():
    port = free_tcp_port()
    proc = _launch(port)
    try:
        _wait_for_listening(port, proc)

        ping_resp = _send(port, {"v": 1, "id": "p1", "op": "ping"})
        assert ping_resp["status"] == "ok"
        assert ping_resp["value"]["session_type"] == "builtin"

        exec_resp = _send(port, {"v": 1, "id": "e1", "op": "exec", "code": "1 + 1"})
        assert exec_resp["status"] == "ok"
        assert exec_resp["value"] == "2"

        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            pytest.fail("bridge subprocess did not exit after SIGTERM")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
