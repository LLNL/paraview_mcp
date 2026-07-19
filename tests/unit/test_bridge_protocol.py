"""Line-level protocol tests: B-06 (happy path), B-10 (ping), B-16 (reset),
B-18 (auth), B-19 (protocol errors). Lines are fed directly to
_process_line(), no real socket involved.
"""
import json


class _FakeConn:
    def __init__(self):
        self.wbuf = b""


def send(bridge, conn, req):
    bridge._process_line(conn, json.dumps(req))
    line, _, rest = conn.wbuf.partition(b"\n")
    conn.wbuf = rest
    return json.loads(line.decode("utf-8"))


def test_normal_request_echoes_v_and_id_with_ok_status(bridge):
    conn = _FakeConn()
    resp = send(bridge, conn, {"v": 1, "id": "abc-123", "op": "ping"})
    assert resp["v"] == 1
    assert resp["id"] == "abc-123"
    assert resp["status"] == "ok"


def test_bad_json_line_yields_protocol_error_with_null_id_and_keeps_connection(bridge):
    conn = _FakeConn()
    bridge._process_line(conn, "{not valid json")
    line1, _, rest = conn.wbuf.partition(b"\n")
    resp1 = json.loads(line1.decode("utf-8"))
    assert resp1["status"] == "error"
    assert resp1["error"]["kind"] == "protocol_error"
    assert resp1["id"] is None

    conn.wbuf = rest
    resp2 = send(bridge, conn, {"v": 1, "id": "next", "op": "ping"})
    assert resp2["status"] == "ok"
    assert resp2["id"] == "next"


def test_unknown_op_is_protocol_error(bridge):
    conn = _FakeConn()
    resp = send(bridge, conn, {"v": 1, "id": "x", "op": "frobnicate"})
    assert resp["status"] == "error"
    assert resp["error"]["kind"] == "protocol_error"


def test_version_mismatch_is_protocol_error(bridge):
    conn = _FakeConn()
    resp = send(bridge, conn, {"v": 2, "id": "x", "op": "ping"})
    assert resp["status"] == "error"
    assert resp["error"]["kind"] == "protocol_error"


def test_token_match_when_bridge_has_token_configured(bridge, monkeypatch):
    monkeypatch.setenv("PARAVIEW_MCP_TOKEN", "secret")
    conn = _FakeConn()
    resp = send(bridge, conn, {"v": 1, "id": "x", "op": "ping", "token": "secret"})
    assert resp["status"] == "ok"


def test_token_mismatch_is_auth_error(bridge, monkeypatch):
    monkeypatch.setenv("PARAVIEW_MCP_TOKEN", "secret")
    conn = _FakeConn()
    resp = send(bridge, conn, {"v": 1, "id": "x", "op": "ping", "token": "wrong"})
    assert resp["status"] == "error"
    assert resp["error"]["kind"] == "auth_error"


def test_missing_token_is_auth_error_when_configured(bridge, monkeypatch):
    monkeypatch.setenv("PARAVIEW_MCP_TOKEN", "secret")
    conn = _FakeConn()
    resp = send(bridge, conn, {"v": 1, "id": "x", "op": "ping"})
    assert resp["status"] == "error"
    assert resp["error"]["kind"] == "auth_error"


def test_token_ignored_when_bridge_has_no_token_configured(bridge, monkeypatch):
    monkeypatch.delenv("PARAVIEW_MCP_TOKEN", raising=False)
    conn = _FakeConn()
    resp = send(bridge, conn, {"v": 1, "id": "x", "op": "ping", "token": "anything"})
    assert resp["status"] == "ok"


def test_auth_error_response_does_not_leak_the_token(bridge, monkeypatch):
    monkeypatch.setenv("PARAVIEW_MCP_TOKEN", "super-secret-value")
    conn = _FakeConn()
    resp = send(bridge, conn, {"v": 1, "id": "x", "op": "ping", "token": "wrong"})
    assert "super-secret-value" not in json.dumps(resp)


def test_ping_value_has_all_b10_fields(bridge):
    conn = _FakeConn()
    resp = send(bridge, conn, {"v": 1, "id": "x", "op": "ping"})
    value = resp["value"]
    for key in ("bridge_version", "paraview_version", "python_version", "session_type", "server"):
        assert key in value
    assert value["session_type"] == "builtin"
    assert value["server"] is None


def test_reset_returns_ok_and_clears_namespace(bridge):
    conn = _FakeConn()
    send(bridge, conn, {"v": 1, "id": "e1", "op": "exec", "code": "leftover = 1"})
    resp = send(bridge, conn, {"v": 1, "id": "r1", "op": "reset"})
    assert resp["status"] == "ok"
    resp2 = send(bridge, conn, {"v": 1, "id": "e2", "op": "exec", "code": "leftover"})
    assert resp2["status"] == "error"
    assert resp2["error"]["type"] == "NameError"
