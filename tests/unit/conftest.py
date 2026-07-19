"""Shared fixtures for bridge unit tests (docs/M1_PLAN.md section 4.1).

Two of the three test seams live here:
  1. Fake paraview/paraview.simple/paraview.servermanager/vtk injected into
     sys.modules before the bridge module is imported, so the bridge's
     lazily-imported `from paraview...`/`import vtk` calls resolve to the
     fakes under tests/fakes/.
  2. A helper to import a *fresh* copy of bridge/paraview_mcp_bridge.py per
     test (its own module namespace, not cached in sys.modules), so each
     test starts with clean globals (_ns, _sel, _listener, ...) regardless
     of what earlier tests did.
"""
import asyncio
import importlib.util
import json
import select as select_mod
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.fakes import paraview as fake_paraview_pkg
from tests.fakes import vtk as fake_vtk
from tests.fakes.paraview import servermanager as fake_servermanager
from tests.fakes.paraview import simple as fake_simple

BRIDGE_PATH = Path(__file__).resolve().parents[2] / "bridge" / "paraview_mcp_bridge.py"


def load_bridge_module():
    """Import a standalone copy of the bridge, isolated from sys.modules
    caching so each call gets its own fresh globals."""
    spec = importlib.util.spec_from_file_location("paraview_mcp_bridge", str(BRIDGE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_paraview(monkeypatch):
    """Inject fake paraview/paraview.simple/paraview.servermanager/vtk into
    sys.modules for the duration of the test. monkeypatch.setitem reverts
    sys.modules automatically at teardown."""
    fake_simple._reset()
    fake_servermanager._reset()
    fake_vtk._reset()
    monkeypatch.setitem(sys.modules, "paraview", fake_paraview_pkg)
    monkeypatch.setitem(sys.modules, "paraview.simple", fake_simple)
    monkeypatch.setitem(sys.modules, "paraview.servermanager", fake_servermanager)
    monkeypatch.setitem(sys.modules, "vtk", fake_vtk)
    yield {"simple": fake_simple, "servermanager": fake_servermanager, "vtk": fake_vtk}


@pytest.fixture
def bridge(fake_paraview):
    """A freshly imported bridge module with fake paraview/vtk in place."""
    return load_bridge_module()


@pytest.fixture
def bridge_no_paraview():
    """A freshly imported bridge module with NO fake paraview/vtk at all
    (B-01 import-safety tests)."""
    return load_bridge_module()


def free_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeBridgeSocketHarness:
    """Drives a bridge module's socket-loop directly over a real loopback
    TCP socket, calling _poll_once() by hand instead of relying on a timer
    or a blocking loop -- seam 2 of docs/M1_PLAN.md 4.1. Used by
    test_bridge_socketloop.py and anything that needs deterministic,
    tick-at-a-time control.
    """

    def __init__(self, bridge_module, port=None):
        self.bridge = bridge_module
        self.port = port if port is not None else free_tcp_port()
        listener = bridge_module._bind_listener(self.port)
        bridge_module._sel.register(listener, __import__("selectors").EVENT_READ, data=None)
        bridge_module._listener = listener

    def poll(self, n=1):
        for _ in range(n):
            self.bridge._poll_once(timeout=0)

    def poll_until(self, predicate, timeout=2.0, interval=0.01):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.poll()
            if predicate():
                return True
            time.sleep(interval)
        return False

    def close(self):
        self.bridge.stop()


@pytest.fixture
def bridge_harness(bridge):
    harness = FakeBridgeSocketHarness(bridge)
    yield harness
    harness.close()


def collect_one_response(harness, client_sock, max_polls=200, poll_timeout=0.01):
    """Poll the bridge (tick-at-a-time) until one full NDJSON line has
    arrived on client_sock, or give up after max_polls."""
    buf = b""
    for _ in range(max_polls):
        harness.poll()
        readable, _, _ = select_mod.select([client_sock], [], [], poll_timeout)
        if readable:
            chunk = client_sock.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
    line, _, rest = buf.partition(b"\n")
    resp = json.loads(line.decode("utf-8")) if line else None
    return resp, rest


def collect_n_responses(harness, client_sock, n, max_polls=400, poll_timeout=0.01):
    buf = b""
    responses = []
    for _ in range(max_polls):
        harness.poll()
        readable, _, _ = select_mod.select([client_sock], [], [], poll_timeout)
        if readable:
            chunk = client_sock.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            responses.append(json.loads(line.decode("utf-8")))
        if len(responses) >= n:
            break
    return responses


def round_trip(harness, client_sock, req, max_polls=200, poll_timeout=0.01):
    client_sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
    return collect_one_response(harness, client_sock, max_polls=max_polls, poll_timeout=poll_timeout)


class PollPump:
    """Drives a bridge module's _poll_once() from a background thread, so
    a test can hold a *real* long-lived listening bridge instance (via
    start()) and hit it with ordinary blocking client sockets from the
    main thread -- e.g. to exercise the startup idempotency probe
    (B-03), which is itself a blocking call. The production bridge never
    uses threads (B-20); this is test infrastructure only.
    """

    def __init__(self, bridge_module, interval=0.02):
        self._bridge = bridge_module
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                self._bridge._poll_once(timeout=self._interval)
            except Exception:
                return

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)


# --------------------------------------------------------------------------
# Fake bridge server (seam 3, docs/M1_PLAN.md 4.1): an in-process asyncio
# NDJSON server that plays a scripted role for bridge_client tests. Real
# TCP, real asyncio -- only the response *content* is scripted, so it
# exercises the client's actual socket/framing code paths.
# --------------------------------------------------------------------------

class FakeBridgeServer:
    def __init__(self):
        self.host = "127.0.0.1"
        self.port = None
        self._server = None
        self.received = []
        self.connections = 0
        self.handler = self._default_handler

    async def start(self, port=0):
        self._server = await asyncio.start_server(self._handle_client, self.host, port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(self, reader, writer):
        self.connections += 1
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.received.append(req)
                await self.handler(req, writer)
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    @staticmethod
    async def _default_handler(req, writer):
        resp = {"v": 1, "id": req.get("id"), "status": "ok",
                "value": {"bridge_version": "test", "paraview_version": "test",
                          "python_version": "3.x", "session_type": "builtin", "server": None},
                "state": None}
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()


@pytest.fixture
async def fake_bridge():
    server = FakeBridgeServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def fake_bridge_factory():
    servers = []

    async def _make(port=0):
        server = FakeBridgeServer()
        await server.start(port=port)
        servers.append(server)
        return server

    yield _make
    for server in servers:
        await server.stop()
