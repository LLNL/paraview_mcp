"""Fixtures for integration tests (docs/M2_PLAN.md I-01/I-02): a real
`pvpython --standalone` running the actual bridge, hit with a real TCP
connection -- no fakes anywhere in this directory.

Scope (docs/M2_PLAN.md I-05): this covers the wire protocol, the exec
engine, and offscreen rendering against real paraview.simple. It does NOT
cover the GUI-only path (timer-driven ticking, Qt interaction, real GPU
rendering) or differences between official binaries and conda-forge
builds -- those remain the manual acceptance (M1_PLAN.md 5) / SMOKE.md
job.
"""
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = REPO_ROOT / "bridge" / "paraview_mcp_bridge.py"

LISTENING_MARKER = "standalone bridge listening"
START_TIMEOUT_S = 30


def _find_pvpython():
    """PARAVIEW_MCP_PVPYTHON env var takes precedence over PATH lookup
    (docs/M2_PLAN.md I-01), so a dev machine with multiple ParaView
    installs (or none on PATH at all) can point at a specific one.
    shutil.which() also validates the override (exists + executable),
    which works for absolute paths too -- a stale/typo'd override should
    make the suite skip cleanly, not fail every test with ENOENT."""
    override = os.environ.get("PARAVIEW_MCP_PVPYTHON")
    if override:
        return shutil.which(override)
    return shutil.which("pvpython")


PVPYTHON = _find_pvpython()


def _free_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StandaloneBridge:
    """Launches `pvpython --standalone` and tears it down reliably.

    pvpython is itself a launcher that forks a "pvpython-real" child
    (verified on ParaView 6.1.1 -- same shape as the `paraview` GUI
    launcher hit during M1_PLAN.md 5 #8's debugging). SIGTERM to just the
    launcher's PID does not reliably reach that child, so the process is
    started in its own session (start_new_session=True) and torn down by
    signalling the whole process group.
    """

    def __init__(self, port):
        self.host = "127.0.0.1"
        self.port = port
        self.process = None

    def start(self, timeout=START_TIMEOUT_S):
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"  # readline() below would otherwise
        # block on pvpython's default block-buffered stdout when it's a
        # pipe, not a TTY (verified empirically -- docs/M2_PLAN.md I-02).
        self.process = subprocess.Popen(
            [PVPYTHON, "--force-offscreen-rendering", str(BRIDGE_PATH),
             "--standalone", "--port", str(self.port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=env, start_new_session=True,
        )
        deadline = time.time() + timeout
        output = []
        while time.time() < deadline:
            line = self.process.stdout.readline()
            if not line:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        "bridge process exited early (code %s):\n%s"
                        % (self.process.returncode, "".join(output)))
                continue
            output.append(line)
            if LISTENING_MARKER in line:
                return
        self.stop()
        raise TimeoutError(
            "bridge did not start listening within %ss:\n%s" % (timeout, "".join(output)))

    def stop(self):
        if self.process is None or self.process.poll() is not None:
            return
        try:
            pgid = os.getpgid(self.process.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
            self.process.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=5)


@pytest.fixture(scope="module")
def standalone_bridge():
    """One bridge process shared by every test in a module -- tests that
    need a clean pipeline call `reset` themselves rather than paying a
    fresh pvpython startup per test."""
    if PVPYTHON is None:
        pytest.skip("no pvpython found (docs/M2_PLAN.md I-01)")
    bridge = StandaloneBridge(_free_tcp_port())
    bridge.start()
    yield bridge
    bridge.stop()
