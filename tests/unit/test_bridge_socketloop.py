"""Real TCP + manual _poll_once() driving: B-03, B-06, B-07, B-08, B-19.

Seam 2 of docs/M1_PLAN.md 4.1 -- no timer, no GUI, fully deterministic.
"""
import json
import selectors
import socket

import pytest

from tests.unit.conftest import (
    PollPump,
    collect_n_responses,
    collect_one_response,
    free_tcp_port,
    round_trip,
)


def connect(port, rcvbuf=None):
    s = socket.create_connection(("127.0.0.1", port), timeout=2)
    s.setblocking(False)
    if rcvbuf:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    return s


# ---- B-06: basic round trip, framing robustness, concurrency ------------

def test_accept_request_response_round_trip(bridge_harness):
    with connect(bridge_harness.port) as client:
        resp, _ = round_trip(bridge_harness, client, {"v": 1, "id": "rt1", "op": "ping"})
        assert resp["status"] == "ok"
        assert resp["id"] == "rt1"


def test_request_split_across_multiple_tcp_segments(bridge_harness):
    with connect(bridge_harness.port) as client:
        payload = (json.dumps({"v": 1, "id": "split1", "op": "ping"}) + "\n").encode("utf-8")
        mid = len(payload) // 2
        client.sendall(payload[:mid])
        bridge_harness.poll(3)
        client.sendall(payload[mid:])
        resp, _ = collect_one_response(bridge_harness, client)
        assert resp["id"] == "split1"
        assert resp["status"] == "ok"


def test_multiple_requests_in_one_segment_all_answered_in_order(bridge_harness):
    with connect(bridge_harness.port) as client:
        reqs = [{"v": 1, "id": "m%d" % i, "op": "ping"} for i in range(5)]
        payload = "".join(json.dumps(r) + "\n" for r in reqs).encode("utf-8")
        client.sendall(payload)
        responses = collect_n_responses(bridge_harness, client, 5)
        assert [r["id"] for r in responses] == ["m0", "m1", "m2", "m3", "m4"]
        assert all(r["status"] == "ok" for r in responses)


def test_multiple_simultaneous_connections(bridge_harness):
    c1 = connect(bridge_harness.port)
    c2 = connect(bridge_harness.port)
    try:
        c1.sendall((json.dumps({"v": 1, "id": "c1", "op": "ping"}) + "\n").encode("utf-8"))
        c2.sendall((json.dumps({"v": 1, "id": "c2", "op": "ping"}) + "\n").encode("utf-8"))
        r1, _ = collect_one_response(bridge_harness, c1)
        r2, _ = collect_one_response(bridge_harness, c2)
        assert r1["id"] == "c1"
        assert r2["id"] == "c2"
    finally:
        c1.close()
        c2.close()


# ---- B-07: large responses, partial writes, disconnect handling ---------

def test_large_response_spans_multiple_ticks(bridge_harness):
    with connect(bridge_harness.port, rcvbuf=65536) as client:
        req = {"v": 1, "id": "big", "op": "exec", "code": "'x' * (8 * 1024 * 1024)",
               "max_value_bytes": 9 * 1024 * 1024}
        resp, _ = round_trip(bridge_harness, client, req, max_polls=3000)
        assert resp["status"] == "ok"
        assert len(resp["value"]) > 8 * 1024 * 1024


def test_client_close_immediately_after_full_response_does_not_crash(bridge_harness):
    with connect(bridge_harness.port) as client:
        resp, _ = round_trip(bridge_harness, client, {"v": 1, "id": "close1", "op": "ping"})
        assert resp["status"] == "ok"
    # client is now closed; the bridge must notice on its next ticks and
    # clean up without raising.
    for _ in range(5):
        bridge_harness.poll()


def test_on_writable_noop_when_peer_already_closed_in_same_tick(bridge_harness):
    """Direct regression test for the M0 _on_writable race (see the
    comment in bridge/paraview_mcp_bridge.py _on_writable): force the
    exact hazardous selector state -- wbuf already drained, key still
    carrying EVENT_WRITE, peer already closed -- and confirm _poll_once()
    doesn't crash when _on_readable's EOF-close runs before _on_writable
    in the same select() pass.
    """
    bridge = bridge_harness.bridge
    with connect(bridge_harness.port) as client:
        client.sendall((json.dumps({"v": 1, "id": "x", "op": "ping"}) + "\n").encode("utf-8"))
        for _ in range(10):
            bridge_harness.poll()
            conns = [k.data for k in bridge._sel.get_map().values() if k.data is not None]
            if conns:
                break
        assert conns, "expected the accepted connection to be registered"
        conn = conns[0]
    # client socket is closed now (the `with` block exited)
    conn.wbuf = b""
    bridge._sel.modify(conn.sock, selectors.EVENT_READ | selectors.EVENT_WRITE, conn)
    bridge_harness.poll(10)  # must not raise; FIN propagation may take a tick or two
    assert conn.closed is True


def test_disconnect_mid_send_drops_only_that_connection(bridge_harness):
    victim = connect(bridge_harness.port, rcvbuf=4096)
    other = connect(bridge_harness.port)
    try:
        big_req = {"v": 1, "id": "big", "op": "exec", "code": "'z' * (4 * 1024 * 1024)",
                   "max_value_bytes": 5 * 1024 * 1024}
        victim.sendall((json.dumps(big_req) + "\n").encode("utf-8"))
        # Let the bridge start (but not finish) sending a large,
        # multi-tick response, without draining the victim's recv
        # buffer -- then yank the connection out from under it.
        for _ in range(20):
            bridge_harness.poll()
        victim.close()
        for _ in range(20):
            bridge_harness.poll()  # must not raise, must not affect `other`

        resp, _ = round_trip(bridge_harness, other, {"v": 1, "id": "still-alive", "op": "ping"})
        assert resp["status"] == "ok"
        assert resp["id"] == "still-alive"
    finally:
        other.close()


# ---- B-19: oversized line ------------------------------------------------

def test_line_too_long_gets_protocol_error_and_connection_closes(bridge_harness):
    bridge = bridge_harness.bridge
    bridge.MAX_LINE_BYTES = 1024
    with connect(bridge_harness.port, rcvbuf=4096) as client:
        req = {"v": 1, "id": "huge", "op": "exec", "code": "'x'" + " " * 4000}
        client.sendall((json.dumps(req) + "\n").encode("utf-8"))
        resp, _ = collect_one_response(bridge_harness, client, max_polls=500)
        assert resp["status"] == "error"
        assert resp["error"]["kind"] == "protocol_error"
        assert resp["error"]["type"] == "LineTooLong"
        for _ in range(20):
            bridge_harness.poll()
        assert client.recv(1) == b""  # bridge closed its side


# ---- B-08: reentry guard --------------------------------------------------

def test_on_tick_reentry_guard_skips_poll_once(bridge, monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "_poll_once", lambda *a, **kw: calls.append(1))
    bridge._running = True
    bridge._on_tick(None, None)
    assert calls == []
    bridge._running = False
    bridge._on_tick(None, None)
    assert calls == [1]


# ---- B-03: idempotent restart --------------------------------------------

def test_start_called_twice_closes_old_listener_and_succeeds(bridge):
    port = free_tcp_port()
    bridge.start(port=port)
    first_listener = bridge._listener
    assert first_listener is not None
    bridge.start(port=port)
    second_listener = bridge._listener
    assert second_listener is not None
    assert second_listener is not first_listener
    assert first_listener.fileno() == -1
    bridge.stop()


def test_start_detects_existing_bridge_and_returns_cleanly(bridge):
    from tests.unit.conftest import load_bridge_module

    port = free_tcp_port()
    bridge.start(port=port)
    pump = PollPump(bridge).start()
    try:
        other = load_bridge_module()
        other.PROBE_TIMEOUT = 1.0
        other.start(port=port)  # must not raise
        assert other._listener is None
    finally:
        pump.stop()
        bridge.stop()


def test_start_raises_friendly_error_when_port_held_by_non_bridge(bridge):
    port = free_tcp_port()
    bridge.PROBE_TIMEOUT = 0.3
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        with pytest.raises(RuntimeError, match="PARAVIEW_MCP_PORT"):
            bridge.start(port=port)
    finally:
        blocker.close()
