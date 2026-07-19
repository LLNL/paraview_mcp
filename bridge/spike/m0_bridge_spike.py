"""
M0 spike bridge for paraview_mcp -- NOT the production bridge.

Paste this whole file into the ParaView Python Shell (View > Python Shell)
and press Enter, or register it as a macro (Macros > Import new macro...)
and run it. It starts listening immediately (see the `start()` call at the
bottom).

Purpose: answer the open questions in docs/DESIGN.md section 13 (M0) before
committing to the timer-driven architecture:

  (a) Does CreateRepeatingTimer + a TimerEvent observer actually drive this
      callback on the GUI main thread, with paraview.simple usable from it?
  (b) Does a TimerEvent re-enter _on_tick while a long `exec` is running
      (e.g. because Render() or a progress dialog pumps the Qt event loop)?
  (c) Does a VTK error raised on a pvserver process (not the GUI process)
      reach this process's vtkOutputWindow?
  (d) How expensive is a cheap pipeline-state summary at ~50 sources?

Drive it with bridge/spike/m0_client.py from a separate terminal.
Procedure: docs/M0_SPIKE.md.

Deliberately not production quality: no auth, no reconnect handling, no
port-conflict idempotency. Single Python Shell session only.
"""
import ast
import json
import selectors
import socket
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

HOST = "127.0.0.1"
PORT = 9911

# Re-pasting/re-running this file into the SAME Python Shell session must
# NOT clobber a still-live listener/timer from a previous run. If it did,
# stop() (called at the top of start()) would have nothing to find and
# clean up: the old socket would stay bound to the port forever, and the
# old repeating timer + TimerEvent observer would keep firing in the
# background indefinitely, competing with whatever runs next. Only
# initialize state on first load; reuse it across re-pastes.
_g = globals()
_sel = _g.get("_sel") or selectors.DefaultSelector()
_ns = _g.get("_ns") or {"__name__": "__paraview_mcp_m0__"}

_listener = _g.get("_listener")
_iren = _g.get("_iren")
_timer_id = _g.get("_timer_id")

_tick_depth = 0  # only meaningful within one call stack; safe to reset
_max_tick_depth = _g.get("_max_tick_depth", 0)
_tick_count = _g.get("_tick_count", 0)
_announced = _g.get("_announced", False)
del _g


class _Conn:
    __slots__ = ("sock", "rbuf", "wbuf", "closed")

    def __init__(self, sock):
        self.sock = sock
        self.rbuf = b""
        self.wbuf = b""
        self.closed = False


def _log(msg):
    print("[M0 %s] %s" % (time.strftime("%H:%M:%S"), msg))


def _init_namespace():
    if "simple" in _ns:
        return
    exec("from paraview.simple import *", _ns)
    exec("from paraview import simple", _ns)
    _log("namespace initialized (paraview.simple imported)")


def _eval_state():
    """Cheap pipeline summary a la DESIGN.md 6.5.

    Deliberately does NOT call GetRepresentation()/GetDisplayProperties():
    those *create* a representation as a side effect if the source has none,
    which corrupts the very state we're trying to read cheaply. Visibility
    is read by walking the view's existing Representations instead.
    """
    t0 = time.perf_counter()
    simple = _ns["simple"]
    sources = simple.GetSources()
    view = simple.GetActiveView()
    visible_inputs = set()
    if view is not None:
        for rep in getattr(view, "Representations", []):
            inp = getattr(rep, "Input", None)
            vis = getattr(rep, "Visibility", 1)
            if inp is not None and vis:
                visible_inputs.add(inp)
    active = simple.GetActiveSource()
    out = []
    for (name, _sid), proxy in list(sources.items())[:50]:
        out.append({
            "name": name,
            "type": proxy.GetXMLName() if hasattr(proxy, "GetXMLName") else type(proxy).__name__,
            "visible": proxy in visible_inputs,
            "active": proxy is active,
        })
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {"sources": out, "count": len(sources), "elapsed_ms": round(elapsed_ms, 3)}


def _run_exec(code, render):
    _init_namespace()
    stdout_buf, stderr_buf = StringIO(), StringIO()
    value = None
    err = None

    import vtk
    ow = vtk.vtkStringOutputWindow()
    old_ow = vtk.vtkOutputWindow.GetInstance()
    vtk.vtkOutputWindow.SetInstance(ow)

    t0 = time.perf_counter()
    try:
        tree = ast.parse(code)
        last_expr = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last_expr = ast.Expression(tree.body.pop().value)
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(compile(tree, "<m0-spike>", "exec"), _ns)
            if last_expr is not None:
                value = eval(compile(last_expr, "<m0-spike>", "eval"), _ns)
        if render:
            try:
                _ns["simple"].Render()
            except Exception:
                pass
    except BaseException as e:
        err = {"type": type(e).__name__, "message": str(e),
               "traceback": traceback.format_exc()[-4000:]}
    finally:
        vtk.vtkOutputWindow.SetInstance(old_ow)
        vtk_messages = ow.GetOutput()

    duration_ms = round((time.perf_counter() - t0) * 1000, 3)
    try:
        json.dumps(value)
        value_out, repr_only = value, False
    except TypeError:
        value_out, repr_only = repr(value), True

    return {
        "ok": err is None,
        "value": value_out,
        "value_repr_only": repr_only,
        "stdout": stdout_buf.getvalue()[-8000:],
        "stderr": stderr_buf.getvalue()[-8000:],
        "vtk_messages": vtk_messages[-8000:],
        "error": err,
        "duration_ms": duration_ms,
    }


def _handle_request(req):
    op = req.get("op")
    if op == "ping":
        return {"status": "ok", "op": "ping", "tick_count": _tick_count,
                "max_tick_depth": _max_tick_depth}
    if op == "exec":
        return {"status": "ok", "op": "exec", **_run_exec(req["code"], req.get("render", True))}
    if op == "state":
        return {"status": "ok", "op": "state", "state": _eval_state()}
    return {"status": "error", "error": "unknown op %r" % (op,)}


def _process_line(conn, line):
    try:
        req = json.loads(line)
    except json.JSONDecodeError as e:
        resp = {"status": "error", "error": "bad json: %s" % e}
    else:
        resp = _handle_request(req)
        resp["id"] = req.get("id")
    conn.wbuf += (json.dumps(resp) + "\n").encode("utf-8")


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
    if not chunk:
        _close_conn(conn)
        return
    conn.rbuf += chunk
    while b"\n" in conn.rbuf:
        line, conn.rbuf = conn.rbuf.split(b"\n", 1)
        if line.strip():
            _process_line(conn, line.decode("utf-8"))
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
    # Downgrade immediately in the same call once the buffer is drained,
    # rather than waiting for a follow-up call: leaving EVENT_WRITE
    # registered for one extra tick after wbuf is already empty is exactly
    # the window in which the peer can close and select() reports EOF
    # (EVENT_READ) and still-writable (EVENT_WRITE) together for this key
    # in one select() call. _on_readable would close+unregister the socket
    # first, and this handler -- called right after, in the same
    # _poll_once iteration -- would then operate on an already-closed fd.
    if not conn.wbuf:
        _sel.modify(conn.sock, selectors.EVENT_READ, conn)


def _on_accept(listener_sock):
    sock, addr = listener_sock.accept()
    sock.setblocking(False)
    _sel.register(sock, selectors.EVENT_READ, _Conn(sock))
    _log("connection from %s" % (addr,))


def _poll_once():
    for key, mask in _sel.select(timeout=0):
        if key.data is None:
            _on_accept(key.fileobj)
        else:
            if mask & selectors.EVENT_READ:
                _on_readable(key)
            if mask & selectors.EVENT_WRITE:
                _on_writable(key)


def _on_tick(obj, event):
    global _tick_depth, _max_tick_depth, _tick_count, _announced
    _tick_depth += 1
    _max_tick_depth = max(_max_tick_depth, _tick_depth)
    _tick_count += 1
    if _tick_depth > 1:
        _log("*** REENTRY DETECTED: tick depth=%d (a TimerEvent fired while "
             "a previous tick was still executing) ***" % _tick_depth)
    if not _announced:
        _announced = True
        _log("bridge active (first tick fired) -- checkpoint (a) confirmed")
    try:
        _poll_once()
    finally:
        _tick_depth -= 1


def stop():
    global _listener, _iren, _timer_id
    if _iren is not None and _timer_id is not None:
        try:
            _iren.DestroyTimer(_timer_id)
        except Exception:
            pass
    _timer_id = None
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
    _log("stopped")


def start(port=PORT):
    global _listener, _iren, _timer_id, _announced, _tick_count, _max_tick_depth
    stop()  # idempotent: safe to re-run start() in the same Shell session

    from paraview import simple
    view = simple.GetActiveView()
    if view is None or not hasattr(view, "GetInteractor"):
        views = simple.GetRenderViews()
        view = views[0] if views else None
    if view is None:
        raise RuntimeError("No RenderView found. Open a render view first "
                            "(e.g. click 'Render View' in the empty viewport).")
    iren = view.GetInteractor()
    if iren is None:
        raise RuntimeError("Active view has no interactor. This spike targets "
                            "the embedded GUI case only (scenario 1/2 in "
                            "docs/M0_SPIKE.md), not headless pvpython.")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((HOST, port))
    listener.listen(5)
    listener.setblocking(False)
    _sel.register(listener, selectors.EVENT_READ, data=None)

    _listener = listener
    _iren = iren
    _announced = False
    _tick_count = 0
    _max_tick_depth = 0
    _timer_id = iren.CreateRepeatingTimer(50)
    iren.AddObserver("TimerEvent", _on_tick)

    _log("listening on %s:%d, timer_id=%s" % (HOST, port, _timer_id))
    _log("waiting for first tick... (run m0_client.py from another terminal)")
    return _timer_id


start()
