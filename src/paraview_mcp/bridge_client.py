"""Async client for the paraview_mcp bridge's NDJSON wire protocol.

Owns everything DESIGN.md 4.2/5.1/5.4 assigns to the server side of the
connection: lazy/persistent connect with retry (C-01), full-call
serialization (C-02), per-request timeout (C-03), discard-stale-response
(C-04), NDJSON framing robustness (C-05), auto-reconnect (C-06), request
construction (C-07), and translating bridge-side protocol/auth failures
into the troubleshooting wording from DESIGN.md 10 (C-08). The C-03/C-06
wording points at get_state (docs/M2_PLAN.md S-10) now that it exists.

This module never lets a bridge failure propagate as anything other than
a BridgeError subclass -- the MCP server (src/paraview_mcp/server.py)
must never crash because ParaView isn't running.
"""
import asyncio
import json
import os
import uuid

PROTOCOL_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9911
MAX_LINE_BYTES = 64 * 1024 * 1024  # DESIGN.md 5.1
RETRY_COUNT = 2  # DESIGN.md 4.2: "リトライ2回(計~2秒)"
RETRY_DELAY_S = 1.0


class BridgeError(Exception):
    """Base for all bridge_client failures. str(e) is always ready to show
    the LLM/user as-is (DESIGN.md 10's wording is baked in at raise time)."""


class BridgeUnavailableError(BridgeError):
    """Could not connect (or reconnect) to the bridge at all."""


class BridgeTimeoutError(BridgeError):
    """The request exceeded its timeout; the bridge may still be running
    it (it cannot be cancelled -- DESIGN.md 5.4)."""


class BridgeDisconnectedError(BridgeError):
    """The connection dropped while a request was in flight."""


class BridgeProtocolError(BridgeError):
    """auth_error / protocol_error / version mismatch from the bridge, or
    a wire-framing violation on our end (DESIGN.md 10)."""


class BridgeClient:
    """One persistent connection to one bridge. Safe to share across
    concurrent tool calls: `call()` serializes them internally (C-02)."""

    def __init__(self, host=None, port=None, token=None):
        self._host = host or os.environ.get("PARAVIEW_MCP_HOST", DEFAULT_HOST)
        self._port = port or int(os.environ.get("PARAVIEW_MCP_PORT", str(DEFAULT_PORT)))
        self._token = token if token is not None else os.environ.get("PARAVIEW_MCP_TOKEN")
        self._lock = asyncio.Lock()
        self._reader = None
        self._writer = None

    @property
    def host(self):
        return self._host

    @property
    def port(self):
        return self._port

    async def close(self):
        async with self._lock:
            self._invalidate_connection()

    # ---- public ops -------------------------------------------------------

    async def ping(self, timeout_s=10):
        return await self.call("ping", timeout_s=timeout_s)

    async def exec(self, code, render=True, timeout_s=120, max_value_bytes=None):
        fields = {"code": code, "render": render}
        if max_value_bytes is not None:
            fields["max_value_bytes"] = max_value_bytes
        return await self.call("exec", timeout_s=timeout_s, **fields)

    async def reset(self, timeout_s=30):
        return await self.call("reset", timeout_s=timeout_s)

    # ---- core round trip ---------------------------------------------------

    async def call(self, op, timeout_s, **fields):
        """Send one request and wait for its matching response.

        Never raises anything but BridgeError subclasses. Fully
        serialized: only one call() is ever mid-flight at a time,
        regardless of how many coroutines invoke it concurrently (C-02).
        """
        async with self._lock:
            await self._ensure_connected()
            req_id = str(uuid.uuid4())
            request = self._build_request(req_id, op, fields)
            await self._send(request)
            try:
                resp = await asyncio.wait_for(self._read_matching_response(req_id), timeout=timeout_s)
            except asyncio.TimeoutError:
                # asyncio.TimeoutError carries no useful message of its own
                # (and, pre-3.11, isn't the same class as builtin
                # TimeoutError -- don't let a bare `except TimeoutError`
                # rewrite silently stop catching it on Python 3.10).
                raise BridgeTimeoutError(
                    "The request timed out after %ss. The bridge cannot cancel "
                    "in-flight execution, so it may still be running (ParaView "
                    "will look frozen while it does). Wait a bit and check "
                    "get_state. The next request will wait for it to finish "
                    "before it can start." % timeout_s
                ) from None
            self._raise_if_protocol_level_error(resp)
            return resp

    def _build_request(self, req_id, op, fields):
        request = {"v": PROTOCOL_VERSION, "id": req_id, "op": op}
        if self._token:
            request["token"] = self._token
        request.update(fields)
        return request

    async def _send(self, request):
        line = (json.dumps(request) + "\n").encode("utf-8")
        try:
            self._writer.write(line)
            await self._writer.drain()
        except (ConnectionError, OSError) as e:
            self._invalidate_connection()
            raise BridgeDisconnectedError(
                "Lost the connection to the bridge while sending the request. "
                "It will reconnect automatically on the next call; if that "
                "also fails, run the paraview_mcp_bridge macro in ParaView "
                "again."
            ) from e

    async def _read_matching_response(self, req_id):
        """Read lines until one with our id shows up, silently discarding
        everything else (C-04): stale responses to a previously
        timed-out call, or any other noise on the wire."""
        while True:
            try:
                raw_line = await self._read_line()
            except asyncio.LimitOverrunError as e:
                self._invalidate_connection()
                raise BridgeProtocolError(
                    "The bridge sent an oversized response line (%s). Closing "
                    "the connection; check that the bridge and this MCP "
                    "server are the same paraview_mcp version." % e
                ) from e
            except (ConnectionError, OSError) as e:
                self._invalidate_connection()
                raise BridgeDisconnectedError(
                    "Lost the connection to the bridge while waiting for a "
                    "response. The result was lost, but execution may have "
                    "completed -- call get_state to check the current "
                    "pipeline state."
                ) from e
            try:
                resp = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(resp, dict) or resp.get("id") != req_id:
                continue
            return resp

    async def _read_line(self):
        raw = await self._reader.readline()
        if not raw.endswith(b"\n"):
            raise ConnectionError("bridge closed the connection")
        return raw

    def _raise_if_protocol_level_error(self, resp):
        """auth_error/protocol_error indicate a broken setup, not a user
        code bug -- translate them (C-08); exec_error passes through
        unchanged so the server can hand traceback/vtk_messages to the
        LLM as-is (S-03)."""
        if resp.get("status") != "error":
            return
        error = resp.get("error") or {}
        kind = error.get("kind")
        if kind == "auth_error":
            raise BridgeProtocolError(
                "Authentication failed talking to the bridge. Check that "
                "PARAVIEW_MCP_TOKEN matches on both the bridge and this MCP "
                "server (or is unset on both), then try again."
            )
        if kind == "protocol_error":
            raise BridgeProtocolError(
                "Protocol error talking to the bridge (%s). Check that the "
                "bridge macro and this MCP server are the same paraview_mcp "
                "version." % error.get("message", "")
            )

    # ---- connection lifecycle ----------------------------------------------

    async def _ensure_connected(self):
        if self._writer is not None and not self._writer.is_closing():
            return
        last_exc = None
        for attempt in range(RETRY_COUNT + 1):
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    self._host, self._port, limit=MAX_LINE_BYTES
                )
                return
            except OSError as e:
                last_exc = e
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY_S)
        raise BridgeUnavailableError(
            "Could not reach the paraview-mcp bridge at %s:%d (%s). In "
            "ParaView, run the paraview_mcp_bridge macro to start it "
            "(Macros -> paraview_mcp_bridge)." % (self._host, self._port, last_exc)
        ) from last_exc

    def _invalidate_connection(self):
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
        self._reader = None
        self._writer = None
