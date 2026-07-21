"""Wire-protocol-level integration tests against a real `pvpython
--standalone` bridge (docs/M2_PLAN.md I-03): raw TCP/NDJSON, no MCP
server or bridge_client involved. This is the first automated check of
the protocol against *real* paraview.simple -- tests/unit's fakes only
mimic its API shape, they can't catch a real-API mismatch.

Scope: docs/M2_PLAN.md I-05 (protocol/exec semantics/offscreen
rendering only -- not the GUI/timer/Qt path).
"""
import json
import socket
import uuid


def _connect(bridge):
    sock = socket.create_connection((bridge.host, bridge.port), timeout=10)
    sock.settimeout(20)
    return sock


def _roundtrip(sock, op, **fields):
    req = {"v": 1, "id": str(uuid.uuid4()), "op": op}
    req.update(fields)
    sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(1 << 20)
        if not chunk:
            raise ConnectionError("bridge closed the connection")
        buf += chunk
    line, _, _ = buf.partition(b"\n")
    return json.loads(line)


def test_ping_reports_real_paraview_version_and_builtin_session(standalone_bridge):
    with _connect(standalone_bridge) as sock:
        resp = _roundtrip(sock, "ping")
    assert resp["status"] == "ok"
    value = resp["value"]
    assert value["session_type"] == "builtin"
    assert value["server"] is None
    assert value["paraview_version"]  # e.g. "6.1.1", not asserting the exact version
    assert value["bridge_version"]


def test_exec_tail_expression_and_persistent_namespace(standalone_bridge):
    with _connect(standalone_bridge) as sock:
        r1 = _roundtrip(sock, "exec", code="Sphere(); Sphere.__name__", render=False)
        assert r1["status"] == "ok"
        assert json.loads(r1["value"]) == "Sphere"

        r2 = _roundtrip(sock, "exec", code="x = 41", render=False)
        assert r2["status"] == "ok"
        assert r2["value"] is None

        r3 = _roundtrip(sock, "exec", code="x + 1", render=False)
        assert r3["status"] == "ok"
        assert json.loads(r3["value"]) == 42  # x from r2 persisted (DESIGN.md 6.1)

        r4 = _roundtrip(sock, "reset")
        assert r4["status"] == "ok"
        r5 = _roundtrip(sock, "exec", code="'x' in dir()", render=False)
        assert json.loads(r5["value"]) is False  # reset cleared it


def test_state_summary_reflects_real_pipeline(standalone_bridge):
    with _connect(standalone_bridge) as sock:
        # `reset` only clears the exec namespace (DESIGN.md 5.2), not the
        # pipeline -- earlier tests in this module-scoped bridge may have
        # left sources behind, so clear those too for a clean "active
        # source" assertion below.
        _roundtrip(sock, "exec", code="[Delete(p) for p in list(GetSources().values())]",
                   render=False)
        resp = _roundtrip(sock, "exec", code="Sphere()", render=False)
    assert resp["status"] == "ok"
    state = resp["state"]
    names = {s["name"] for s in state["sources"]}
    assert "Sphere1" in names
    sphere = next(s for s in state["sources"] if s["name"] == "Sphere1")
    assert sphere["type"] == "SphereSource"
    assert sphere["active"] is True


def test_real_vtk_error_is_captured_in_vtk_messages(standalone_bridge):
    # A negative sphere resolution is invalid and real VTK reports it
    # through vtkErrorMacro, not a Python exception -- this is the
    # capture path DESIGN.md 6.3 exists for; the fakes can't exercise it
    # because they don't run real VTK code.
    code = (
        "import tempfile, os\n"
        "__fd, __path = tempfile.mkstemp(suffix='.vtk')\n"
        "os.close(__fd)\n"
        "with open(__path, 'w') as __f:\n"
        "    __f.write('not a valid legacy vtk file\\n')\n"
        "try:\n"
        "    __r = OpenDataFile(__path)\n"
        "    __r.UpdatePipeline()\n"
        "finally:\n"
        "    os.remove(__path)\n"
    )
    with _connect(standalone_bridge) as sock:
        _roundtrip(sock, "reset")
        resp = _roundtrip(sock, "exec", code=code, render=False)
    # Whether this surfaces as a Python exception (exec_error) or a VTK
    # message depends on exactly where the malformed file is rejected
    # (DESIGN.md 6.3's note on this ambiguity, confirmed during M0) --
    # assert only that ONE of the two error-reporting paths fired, since
    # both are valid depending on the reader chosen for this content.
    assert resp["status"] == "error" or resp["vtk_messages"]


def test_bad_json_line_gets_protocol_error_and_connection_stays_up(standalone_bridge):
    with _connect(standalone_bridge) as sock:
        sock.sendall(b"not even json\n")
        buf = b""
        while b"\n" not in buf:
            buf += sock.recv(1 << 20)
        resp = json.loads(buf.partition(b"\n")[0])
        assert resp["status"] == "error"
        assert resp["error"]["kind"] == "protocol_error"

        # connection must still be usable afterwards (B-19)
        resp2 = _roundtrip(sock, "ping")
        assert resp2["status"] == "ok"
