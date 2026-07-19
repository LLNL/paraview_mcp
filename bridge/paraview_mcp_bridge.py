"""
paraview_mcp bridge -- runs inside ParaView's own Python (embedded GUI or
pvpython), stdlib only. See docs/DESIGN.md sections 3-6 for the full spec;
this file implements wire protocol v1 and is frozen for the v1 lifetime
(docs/M1_PLAN.md section 1.1) -- new tool-level behavior belongs in the
MCP server's canned exec snippets, not here.

Usage (embedded, inside the ParaView GUI):
    Register this file as a macro (Macros -> Import new macro...) and run
    it, or paste the whole file into View -> Python Shell and press Enter.
    Either way it starts listening immediately: __name__ == "__main__" in
    both the Shell and macro execution contexts, which is what triggers
    start() at the bottom of this file. Re-running is safe (idempotent):
    it closes its own previous listener/timer first.

Usage (standalone, headless / CI):
    pvpython --force-offscreen-rendering paraview_mcp_bridge.py --standalone [--port N]

Importing this module (e.g. `import paraview_mcp_bridge`) does neither:
it only defines functions. paraview/vtk are imported lazily, inside the
functions that need them, so the module can be imported in a plain
CPython environment with no ParaView installed at all (see docs/M1_PLAN.md
section 4.1, test_bridge_import.py).
"""
import ast
import json
import os
import selectors
import socket
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

HOST = "127.0.0.1"
DEFAULT_PORT = 9911
PROTOCOL_VERSION = 1
BRIDGE_VERSION = "1.0.0"

MAX_LINE_BYTES = 64 * 1024 * 1024  # 64 MiB, DESIGN.md 5.1
DEFAULT_MAX_VALUE_BYTES = 256 * 1024  # DESIGN.md 5.2 / 6.2
MAX_OUTPUT_BYTES = 64 * 1024  # stdout/stderr, DESIGN.md 6.3
PROBE_TIMEOUT = 2.0  # startup idempotency ping, DESIGN.md 4.1

# Both "paste into the Python Shell" and "run as a macro" re-execute this
# file's top-level code against a namespace that may already hold globals
# from a previous run in the SAME session. If we blindly created fresh
# objects here, stop() (called at the top of start()) would have nothing
# to find: the old listener socket would stay bound forever and the old
# repeating timer would keep firing in the background. Reuse state across
# re-execution instead of clobbering it.
_g = globals()
_sel = _g.get("_sel") or selectors.DefaultSelector()
_ns = _g.get("_ns") or {"__name__": "__paraview_mcp__"}
_listener = _g.get("_listener")
_iren = _g.get("_iren")
_timer_id = _g.get("_timer_id")
_running = False
_announced = _g.get("_announced", False)
del _g


class _Conn:
    __slots__ = ("sock", "rbuf", "wbuf", "closed", "close_after_write")

    def __init__(self, sock):
        self.sock = sock
        self.rbuf = b""
        self.wbuf = b""
        self.closed = False
        self.close_after_write = False


def _log(msg):
    print("[paraview-mcp %s] %s" % (time.strftime("%H:%M:%S"), msg))


def _truncate_text(s, max_bytes):
    raw = s.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return s
    return raw[:max_bytes].decode("utf-8", errors="ignore") + "…(truncated)"


# --------------------------------------------------------------------------
# Auth (B-18)
# --------------------------------------------------------------------------

def _check_auth(req):
    expected = os.environ.get("PARAVIEW_MCP_TOKEN")
    if not expected:
        return True
    provided = req.get("token")
    return isinstance(provided, str) and provided == expected


# --------------------------------------------------------------------------
# exec engine (B-11..B-16)
# --------------------------------------------------------------------------

def _init_namespace():
    if "simple" in _ns:
        return
    exec("from paraview.simple import *", _ns)
    exec("from paraview import simple, servermanager", _ns)
    _log("namespace initialized (paraview.simple imported)")


def _reset_namespace():
    _ns.clear()
    _ns["__name__"] = "__paraview_mcp__"


def _serialize_value(value, max_bytes):
    try:
        text = json.dumps(value)
        is_json = True
    except (TypeError, ValueError):
        text = repr(value)
        is_json = False
    return _truncate_text(text, max_bytes), is_json


def _run_exec(code, render, max_value_bytes):
    _init_namespace()
    stdout_buf, stderr_buf = StringIO(), StringIO()
    value = None
    err = None

    import vtk
    ow = vtk.vtkStringOutputWindow()
    old_ow = vtk.vtkOutputWindow.GetInstance()
    vtk.vtkOutputWindow.SetInstance(ow)
    try:
        try:
            tree = ast.parse(code)
            last_expr = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last_expr = ast.Expression(tree.body.pop().value)
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(compile(tree, "<paraview-mcp>", "exec"), _ns)
                if last_expr is not None:
                    value = eval(compile(last_expr, "<paraview-mcp>", "eval"), _ns)
            if render:
                try:
                    _ns["simple"].Render()
                except Exception:
                    pass
        except BaseException as e:
            tb_text = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            tb_lines = tb_text.splitlines()
            if len(tb_lines) > 40:
                tb_text = "\n".join(tb_lines[-40:])
            err = {"kind": "exec_error", "type": type(e).__name__,
                   "message": str(e), "traceback": tb_text}
    finally:
        vtk.vtkOutputWindow.SetInstance(old_ow)
        vtk_messages = ow.GetOutput()

    if value is None:
        value_out, value_is_json = None, True
    else:
        value_out, value_is_json = _serialize_value(value, max_value_bytes)

    return {
        "value": value_out,
        "value_is_json": value_is_json,
        "stdout": _truncate_text(stdout_buf.getvalue(), MAX_OUTPUT_BYTES),
        "stderr": _truncate_text(stderr_buf.getvalue(), MAX_OUTPUT_BYTES),
        "vtk_messages": vtk_messages,
        "error": err,
    }


# --------------------------------------------------------------------------
# state summary (B-17)
# --------------------------------------------------------------------------

def _eval_state():
    """Cheap pipeline summary, DESIGN.md 6.5.

    GetSources() is deliberately NOT individually try/excepted: if it
    raises, the whole state is not worth reporting piecemeal, so the
    exception propagates and the caller (_process_line) turns the whole
    state into null. Every other field IS individually try/excepted so a
    single failing lookup (e.g. no active view) doesn't blank the rest.

    Does NOT call GetRepresentation()/GetDisplayProperties(): those
    *create* a representation as a side effect if the source has none,
    corrupting the very state being read. Visibility is read by walking
    the view's existing Representations instead. No server round-trip
    calls (e.g. GetDataInformation) -- those are get_state(detail=...)'s
    job (M2), not this per-response summary's.
    """
    from paraview import simple

    sources_dict = simple.GetSources()

    try:
        active_source = simple.GetActiveSource()
    except Exception:
        active_source = None

    try:
        view = simple.GetActiveView()
    except Exception:
        view = None

    visible_inputs = set()
    if view is not None:
        try:
            for rep in getattr(view, "Representations", []):
                inp = getattr(rep, "Input", None)
                vis = getattr(rep, "Visibility", 1)
                if inp is not None and vis:
                    visible_inputs.add(inp)
        except Exception:
            pass

    items = list(sources_dict.items())
    truncated = len(items) > 50
    if truncated:
        items = items[:50]
    sources_out = []
    for (name, _sid), proxy in items:
        try:
            type_name = proxy.GetXMLName() if hasattr(proxy, "GetXMLName") else type(proxy).__name__
        except Exception:
            type_name = type(proxy).__name__
        sources_out.append({
            "name": name,
            "type": type_name,
            "visible": proxy in visible_inputs,
            "active": proxy is active_source,
        })

    view_out = None
    if view is not None:
        try:
            size = list(view.ViewSize) if hasattr(view, "ViewSize") else None
            vtype = view.GetXMLName() if hasattr(view, "GetXMLName") else type(view).__name__
            view_out = {"type": vtype, "size": size}
        except Exception:
            view_out = None

    time_out = None
    try:
        scene = simple.GetAnimationScene()
        tk = scene.TimeKeeper
        steps = list(tk.TimestepValues) if tk.TimestepValues else []
        time_out = {
            "value": float(scene.TimeKeeper.Time),
            "range": [float(steps[0]), float(steps[-1])] if steps else None,
            "n_steps": len(steps),
        }
    except Exception:
        time_out = None

    return {
        "sources": sources_out,
        "count": len(sources_dict),
        "truncated": truncated,
        "view": view_out,
        "time": time_out,
    }


# --------------------------------------------------------------------------
# ping (B-10)
# --------------------------------------------------------------------------

def _paraview_version():
    try:
        from paraview import servermanager
        pm = servermanager.vtkSMProxyManager
        return "%s.%s.%s" % (pm.GetVersionMajor(), pm.GetVersionMinor(), pm.GetVersionPatch())
    except Exception:
        return "unknown"


def _session_info():
    try:
        from paraview import servermanager
        conn = servermanager.ActiveConnection
        if conn is None:
            return "builtin", None
        if conn.IsRemote():
            host = getattr(conn, "ds_host", None)
            port = getattr(conn, "ds_port", None)
            server = "%s:%s" % (host, port) if host else "remote"
            return "client-server", server
        return "builtin", None
    except Exception:
        return "unknown", None


def _ping_value():
    session_type, server = _session_info()
    return {
        "bridge_version": BRIDGE_VERSION,
        "paraview_version": _paraview_version(),
        "python_version": "%d.%d.%d" % tuple(sys.version_info[:3]),
        "session_type": session_type,
        "server": server,
    }


# --------------------------------------------------------------------------
# protocol dispatch (B-19)
# --------------------------------------------------------------------------

def _dispatch(req):
    """Returns (status, fields). Never raises -- callers rely on that."""
    if req.get("v") != PROTOCOL_VERSION:
        return "error", {"error": {
            "kind": "protocol_error", "type": "VersionMismatch",
            "message": "unsupported protocol version %r (bridge speaks %d)"
                       % (req.get("v"), PROTOCOL_VERSION)}}
    if not _check_auth(req):
        return "error", {"error": {
            "kind": "auth_error", "type": "AuthError",
            "message": "missing or incorrect token"}}

    op = req.get("op")
    if op == "ping":
        return "ok", {"value": _ping_value()}
    if op == "exec":
        if "code" not in req:
            return "error", {"error": {
                "kind": "protocol_error", "type": "MissingField",
                "message": "exec requires a 'code' field"}}
        result = _run_exec(req["code"], req.get("render", True),
                            req.get("max_value_bytes", DEFAULT_MAX_VALUE_BYTES))
        fields = {"stdout": result["stdout"], "stderr": result["stderr"],
                  "vtk_messages": result["vtk_messages"]}
        if result["error"] is not None:
            fields["error"] = result["error"]
            return "error", fields
        fields["value"] = result["value"]
        fields["value_is_json"] = result["value_is_json"]
        return "ok", fields
    if op == "reset":
        _reset_namespace()
        return "ok", {}
    return "error", {"error": {
        "kind": "protocol_error", "type": "UnknownOp",
        "message": "unknown op %r" % (op,)}}


def _attach_state(resp):
    try:
        resp["state"] = _eval_state()
    except Exception:
        resp["state"] = None
    return resp


def _process_line(conn, line):
    t0 = time.perf_counter()
    try:
        req = json.loads(line)
        if not isinstance(req, dict):
            raise ValueError("request must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        resp = {"v": PROTOCOL_VERSION, "id": None, "status": "error",
                "error": {"kind": "protocol_error", "type": "BadRequest", "message": str(e)}}
    else:
        status, fields = _dispatch(req)
        resp = {"v": PROTOCOL_VERSION, "id": req.get("id"), "status": status}
        resp.update(fields)
    resp["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    _attach_state(resp)
    conn.wbuf += (json.dumps(resp) + "\n").encode("utf-8")


def _send_line_too_long(conn):
    resp = {"v": PROTOCOL_VERSION, "id": None, "status": "error",
            "error": {"kind": "protocol_error", "type": "LineTooLong",
                      "message": "request line exceeds %d bytes" % MAX_LINE_BYTES},
            "duration_ms": 0}
    _attach_state(resp)
    conn.wbuf += (json.dumps(resp) + "\n").encode("utf-8")


# --------------------------------------------------------------------------
# socket I/O (B-06, B-07)
# --------------------------------------------------------------------------

def _close_conn(conn):
    if conn.closed:
        return
    conn.closed = True
    try:
        _sel.unregister(conn.sock)
    except (KeyError, ValueError):
        pass
    conn.sock.close()


def _on_readable(key):
    conn = key.data
    if conn.closed:
        return
    try:
        chunk = conn.sock.recv(65536)
    except BlockingIOError:
        return
    except OSError:
        _close_conn(conn)
        return
    if not chunk:
        _close_conn(conn)
        return
    conn.rbuf += chunk
    while b"\n" in conn.rbuf:
        line, conn.rbuf = conn.rbuf.split(b"\n", 1)
        if len(line) > MAX_LINE_BYTES:
            _send_line_too_long(conn)
            conn.close_after_write = True
            conn.rbuf = b""
            break
        if line.strip():
            _process_line(conn, line.decode("utf-8", errors="replace"))
    else:
        if len(conn.rbuf) > MAX_LINE_BYTES:
            _send_line_too_long(conn)
            conn.close_after_write = True
            conn.rbuf = b""
    if conn.wbuf:
        _sel.modify(conn.sock, selectors.EVENT_READ | selectors.EVENT_WRITE, conn)


def _on_writable(key):
    conn = key.data
    if conn.closed:
        return
    if conn.wbuf:
        try:
            sent = conn.sock.send(conn.wbuf)
            conn.wbuf = conn.wbuf[sent:]
        except BlockingIOError:
            return
        except OSError:
            _close_conn(conn)
            return
    # Downgrade immediately in the same call once the buffer is drained,
    # rather than waiting for a follow-up call: leaving EVENT_WRITE
    # registered for one extra tick after wbuf is already empty is exactly
    # the window in which the peer can close and select() reports EOF
    # (EVENT_READ) and still-writable (EVENT_WRITE) together for this key
    # in one select() call. _on_readable would close+unregister the socket
    # first, and this handler -- called right after, in the same
    # _poll_once iteration -- would then operate on an already-closed fd.
    if not conn.wbuf:
        if conn.close_after_write:
            _close_conn(conn)
        else:
            _sel.modify(conn.sock, selectors.EVENT_READ, conn)


def _on_accept(listener_sock):
    sock, addr = listener_sock.accept()
    sock.setblocking(False)
    _sel.register(sock, selectors.EVENT_READ, _Conn(sock))
    _log("connection from %s" % (addr,))


def _poll_once(timeout=0):
    for key, mask in _sel.select(timeout=timeout):
        if key.data is None:
            _on_accept(key.fileobj)
        else:
            if mask & selectors.EVENT_READ:
                _on_readable(key)
            if mask & selectors.EVENT_WRITE:
                _on_writable(key)


def _on_tick(obj, event):
    """TimerEvent callback (embedded mode only). Reentry guard: B-08."""
    global _running, _announced
    if _running:
        return
    _running = True
    try:
        if not _announced:
            _announced = True
            _log("bridge active (first tick fired)")
        _poll_once()
    finally:
        _running = False


# --------------------------------------------------------------------------
# startup / shutdown (B-02, B-03, B-05, B-09)
# --------------------------------------------------------------------------

def _default_port():
    val = os.environ.get("PARAVIEW_MCP_PORT")
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return DEFAULT_PORT


def _find_render_view():
    from paraview import simple
    view = simple.GetActiveView()
    if view is None or not hasattr(view, "GetInteractor"):
        views = simple.GetRenderViews()
        view = views[0] if views else None
    return view


def _bind_listener(port):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((HOST, port))
    except OSError:
        listener.close()
        raise
    listener.listen(5)
    listener.setblocking(False)
    return listener


def _probe_existing_bridge(port, timeout=None):
    if timeout is None:
        timeout = PROBE_TIMEOUT
    try:
        with socket.create_connection((HOST, port), timeout=timeout) as s:
            s.sendall((json.dumps({"v": PROTOCOL_VERSION, "id": "startup-probe",
                                    "op": "ping"}) + "\n").encode("utf-8"))
            s.settimeout(timeout)
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    return False
                buf += chunk
        line, _, _ = buf.partition(b"\n")
        resp = json.loads(line.decode("utf-8"))
        value = resp.get("value")
        return resp.get("status") == "ok" and isinstance(value, dict) and "bridge_version" in value
    except Exception:
        return False


def _bind_or_recover(port):
    """Bind the listener, or detect that a bridge already owns the port.

    Returns a bound, listening, non-blocking socket, or None if an
    existing paraview-mcp bridge already answers on the port (idempotent
    restart: nothing more to do). Raises RuntimeError if the port is held
    by something else entirely.
    """
    try:
        return _bind_listener(port)
    except OSError as e:
        if _probe_existing_bridge(port):
            _log("bridge already listening on %s:%d; nothing to do "
                 "(close ParaView first if you need a clean restart)" % (HOST, port))
            return None
        raise RuntimeError(
            "Port %d is already in use by another process. Set "
            "PARAVIEW_MCP_PORT to a different value (matching the MCP "
            "server's setting) and try again." % port
        ) from e


def stop():
    global _listener, _iren, _timer_id
    if _iren is not None and _timer_id is not None:
        try:
            _iren.DestroyTimer(_timer_id)
        except Exception:
            pass
    _timer_id = None
    _iren = None
    if _listener is not None:
        try:
            _sel.unregister(_listener)
        except Exception:
            pass
        try:
            _listener.close()
        except Exception:
            pass
        _listener = None


def start(port=None):
    """Embedded mode: register a GUI-timer-driven listener. Idempotent."""
    global _listener, _iren, _timer_id, _announced, _running
    stop()
    if port is None:
        port = _default_port()

    listener = _bind_or_recover(port)
    if listener is None:
        return

    view = _find_render_view()
    if view is None:
        listener.close()
        raise RuntimeError(
            "No RenderView found. Open a render view first (e.g. click "
            "'Render View' in the empty viewport) and re-run the macro."
        )
    iren = view.GetInteractor()
    if iren is None:
        listener.close()
        raise RuntimeError(
            "Active view has no interactor. start() targets the embedded "
            "GUI case; use --standalone under pvpython for headless runs."
        )

    _sel.register(listener, selectors.EVENT_READ, data=None)
    _listener = listener
    _iren = iren
    _announced = False
    _running = False
    _timer_id = iren.CreateRepeatingTimer(50)
    iren.AddObserver("TimerEvent", _on_tick)
    _log("listening on %s:%d" % (HOST, port))


def start_standalone(port=None):
    """Standalone mode: blocking select loop, no GUI/timer dependency."""
    global _listener, _running
    stop()
    if port is None:
        port = _default_port()

    listener = _bind_or_recover(port)
    if listener is None:
        return

    _sel.register(listener, selectors.EVENT_READ, data=None)
    _listener = listener
    _running = False
    _log("standalone bridge listening on %s:%d" % (HOST, port))
    try:
        while True:
            _poll_once(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop()
        _log("standalone bridge stopped")


def _main():
    argv = sys.argv[1:]
    if "--standalone" in argv:
        port = None
        if "--port" in argv:
            idx = argv.index("--port")
            try:
                port = int(argv[idx + 1])
            except (IndexError, ValueError):
                _log("--port requires an integer argument; ignoring")
        start_standalone(port=port)
    else:
        start()


if __name__ == "__main__":
    _main()
