"""bridge_client tests against the in-process fake bridge server
(seam 3, docs/M1_PLAN.md 4.1): C-01 through C-08.
"""
import asyncio
import json

import pytest

from paraview_mcp import bridge_client as bc


def make_client(server, **kw):
    return bc.BridgeClient(host=server.host, port=server.port, **kw)


# ---- C-05 / C-07: framing + request construction -------------------------

async def test_normal_round_trip_and_request_fields(fake_bridge):
    client = make_client(fake_bridge, token="tok-123")
    resp = await client.exec("1 + 1", max_value_bytes=999)
    assert resp["status"] == "ok"
    sent = fake_bridge.received[0]
    assert sent["v"] == 1
    assert "id" in sent and sent["id"]
    assert sent["token"] == "tok-123"
    assert sent["max_value_bytes"] == 999
    assert sent["op"] == "exec"


async def test_response_delivered_one_byte_at_a_time_is_still_assembled(fake_bridge):
    async def byte_at_a_time(req, writer):
        resp = {"v": 1, "id": req["id"], "status": "ok", "value": "482", "value_is_json": True}
        raw = (json.dumps(resp) + "\n").encode("utf-8")
        for b in raw:
            writer.write(bytes([b]))
            await writer.drain()

    fake_bridge.handler = byte_at_a_time
    client = make_client(fake_bridge)
    resp = await client.call("ping", timeout_s=5)
    assert resp["value"] == "482"


# ---- C-03 / C-04: timeout + stale-response discard ------------------------

async def test_timeout_then_stale_response_discarded_next_call_succeeds(fake_bridge):
    call_count = 0

    async def slow_first_call(req, writer):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await asyncio.sleep(0.4)
        resp = {"v": 1, "id": req["id"], "status": "ok", "value": None}
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()

    fake_bridge.handler = slow_first_call
    client = make_client(fake_bridge)

    with pytest.raises(bc.BridgeTimeoutError):
        await client.call("ping", timeout_s=0.1)

    resp = await client.call("ping", timeout_s=2)
    assert resp["status"] == "ok"


async def test_noise_and_mismatched_id_lines_are_discarded(fake_bridge):
    async def noisy(req, writer):
        writer.write(b'{"v": 1, "id": "not-mine", "status": "ok"}\n')
        writer.write(b"not even json\n")
        await writer.drain()
        resp = {"v": 1, "id": req["id"], "status": "ok", "value": "42"}
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()

    fake_bridge.handler = noisy
    client = make_client(fake_bridge)
    resp = await client.call("ping", timeout_s=2)
    assert resp["value"] == "42"


# ---- C-01: connection failure / retry --------------------------------------

async def test_connection_refused_retries_then_raises_bridge_unavailable(monkeypatch):
    monkeypatch.setattr(bc, "RETRY_DELAY_S", 0.01)
    client = bc.BridgeClient(host="127.0.0.1", port=1)  # nothing listens on port 1
    with pytest.raises(bc.BridgeUnavailableError, match="macro"):
        await client.call("ping", timeout_s=2)


# ---- C-06: reconnect + mid-flight disconnect -------------------------------

async def test_auto_reconnects_after_being_disconnected(fake_bridge_factory):
    server1 = await fake_bridge_factory()
    client = bc.BridgeClient(host=server1.host, port=server1.port)
    resp = await client.call("ping", timeout_s=2)
    assert resp["status"] == "ok"
    await server1.stop()

    server2 = await fake_bridge_factory(port=server1.port)
    client._writer.close()  # deterministically mark the old connection dead
    resp2 = await client.call("ping", timeout_s=2)
    assert resp2["status"] == "ok"
    assert server2.received


async def test_disconnect_while_waiting_raises_with_recovery_wording(fake_bridge):
    async def vanish(req, writer):
        writer.close()

    fake_bridge.handler = vanish
    client = make_client(fake_bridge)
    with pytest.raises(bc.BridgeDisconnectedError, match="get_state"):
        await client.call("ping", timeout_s=2)


# ---- C-02: serialization ----------------------------------------------------

async def test_concurrent_calls_are_serialized_to_one_in_flight(fake_bridge):
    current = 0
    max_observed = 0

    async def tracking(req, writer):
        nonlocal current, max_observed
        current += 1
        max_observed = max(max_observed, current)
        await asyncio.sleep(0.02)
        resp = {"v": 1, "id": req["id"], "status": "ok", "value": None}
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()
        current -= 1

    fake_bridge.handler = tracking
    client = make_client(fake_bridge)
    await asyncio.gather(*[client.call("ping", timeout_s=5) for _ in range(5)])
    assert max_observed == 1


# ---- C-08: protocol-level error translation --------------------------------

async def test_auth_error_response_is_translated(fake_bridge):
    async def auth_fail(req, writer):
        resp = {"v": 1, "id": req["id"], "status": "error",
                "error": {"kind": "auth_error", "type": "AuthError", "message": "bad token"}}
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()

    fake_bridge.handler = auth_fail
    client = make_client(fake_bridge)
    with pytest.raises(bc.BridgeProtocolError, match="PARAVIEW_MCP_TOKEN"):
        await client.call("ping", timeout_s=2)


async def test_protocol_error_response_is_translated(fake_bridge):
    async def proto_fail(req, writer):
        resp = {"v": 1, "id": req["id"], "status": "error",
                "error": {"kind": "protocol_error", "type": "VersionMismatch", "message": "nope"}}
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()

    fake_bridge.handler = proto_fail
    client = make_client(fake_bridge)
    with pytest.raises(bc.BridgeProtocolError, match="version"):
        await client.call("ping", timeout_s=2)


async def test_exec_error_passes_through_unchanged(fake_bridge):
    async def exec_fail(req, writer):
        resp = {"v": 1, "id": req["id"], "status": "error",
                "error": {"kind": "exec_error", "type": "ZeroDivisionError",
                          "message": "division by zero", "traceback": "Traceback..."}}
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()

    fake_bridge.handler = exec_fail
    client = make_client(fake_bridge)
    resp = await client.call("exec", timeout_s=2, code="1/0")
    assert resp["status"] == "error"
    assert resp["error"]["kind"] == "exec_error"
