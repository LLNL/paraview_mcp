"""Exec engine tests: B-11 through B-17. Fake paraview/vtk injected,
handlers called directly (no socket involved) via bridge._dispatch().
"""
import json

from tests.fakes.paraview import simple as fake_simple


def do_exec(bridge, code, **kw):
    req = {"v": 1, "id": "t1", "op": "exec", "code": code}
    req.update(kw)
    return bridge._dispatch(req)


# ---- B-11 / B-12: tail-expression value + serialization -----------------

def test_tail_expression_returns_json_value(bridge):
    status, fields = do_exec(bridge, "1 + 1")
    assert status == "ok"
    assert fields["value"] == "2"
    assert fields["value_is_json"] is True


def test_unserializable_tail_value_falls_back_to_repr(bridge):
    status, fields = do_exec(bridge, "import tests.fakes.paraview.simple as s\ns.FakeUnserializableProxy()")
    assert status == "ok"
    assert fields["value_is_json"] is False
    assert "FakeUnserializableProxy" in fields["value"]


def test_statement_only_yields_null_value(bridge):
    status, fields = do_exec(bridge, "x = 1")
    assert status == "ok"
    assert fields["value"] is None


def test_empty_code_yields_null_value(bridge):
    status, fields = do_exec(bridge, "")
    assert status == "ok"
    assert fields["value"] is None


# ---- B-14: exceptions -----------------------------------------------------

def test_syntax_error_becomes_exec_error(bridge):
    status, fields = do_exec(bridge, "def broken(:\n")
    assert status == "error"
    assert fields["error"]["kind"] == "exec_error"
    assert fields["error"]["type"] == "SyntaxError"


def test_runtime_exception_becomes_exec_error(bridge):
    status, fields = do_exec(bridge, "1 / 0")
    assert status == "error"
    assert fields["error"]["type"] == "ZeroDivisionError"


def test_traceback_capped_to_40_lines(bridge):
    code = (
        "def f(n):\n"
        "    if n == 0:\n"
        "        raise RuntimeError('boom')\n"
        "    f(n - 1)\n"
        "f(80)\n"
    )
    status, fields = do_exec(bridge, code)
    assert status == "error"
    assert len(fields["error"]["traceback"].splitlines()) <= 40


def test_system_exit_is_caught_as_exec_error(bridge):
    status, fields = do_exec(bridge, "raise SystemExit(1)")
    assert status == "error"
    assert fields["error"]["type"] == "SystemExit"


def test_keyboard_interrupt_is_caught_as_exec_error(bridge):
    status, fields = do_exec(bridge, "raise KeyboardInterrupt()")
    assert status == "error"
    assert fields["error"]["type"] == "KeyboardInterrupt"


# ---- B-11 / B-16: namespace persistence and reset ------------------------

def test_namespace_persists_across_exec_calls(bridge):
    do_exec(bridge, "shared_var = 42")
    status, fields = do_exec(bridge, "shared_var")
    assert status == "ok"
    assert fields["value"] == "42"


def test_reset_clears_namespace(bridge):
    do_exec(bridge, "shared_var = 42")
    status, fields = bridge._dispatch({"v": 1, "id": "r1", "op": "reset"})
    assert status == "ok"
    status, fields = do_exec(bridge, "shared_var")
    assert status == "error"
    assert fields["error"]["type"] == "NameError"


def test_dunder_name_is_set_in_namespace(bridge):
    status, fields = do_exec(bridge, "__name__")
    assert status == "ok"
    assert fields["value"] == json.dumps("__paraview_mcp__")


# ---- B-13: stdout/stderr/vtk_messages capture ----------------------------

def test_stdout_and_stderr_are_captured(bridge):
    code = "import sys\nprint('hello-out')\nsys.stderr.write('hello-err')\n'done'"
    status, fields = do_exec(bridge, code)
    assert status == "ok"
    assert "hello-out" in fields["stdout"]
    assert "hello-err" in fields["stderr"]


def test_stdout_truncated_to_64kib(bridge):
    code = "print('x' * 200000)"
    status, fields = do_exec(bridge, code)
    assert status == "ok"
    assert len(fields["stdout"].encode("utf-8")) <= 65536 + len("…(truncated)".encode())
    assert fields["stdout"].endswith("…(truncated)")


def test_value_truncated_at_default_256kib(bridge):
    code = "'y' * 400000"
    status, fields = do_exec(bridge, code)
    assert status == "ok"
    assert fields["value"].endswith("…(truncated)")
    assert len(fields["value"].encode("utf-8")) <= 262144 + len("…(truncated)".encode())


def test_max_value_bytes_lets_large_values_through(bridge):
    code = "'y' * 400000"
    status, fields = do_exec(bridge, code, max_value_bytes=1024 * 1024)
    assert status == "ok"
    assert not fields["value"].endswith("…(truncated)")
    assert len(fields["value"]) > 262144


def test_vtk_error_lands_in_vtk_messages(bridge):
    code = "import vtk\nvtk.fake_vtk_error('kaboom')\n'ok'"
    status, fields = do_exec(bridge, code)
    assert status == "ok"
    assert "kaboom" in fields["vtk_messages"]


def test_vtk_output_window_restored_after_exception(bridge, fake_paraview):
    vtk_mod = fake_paraview["vtk"]
    before = vtk_mod.vtkOutputWindow.GetInstance()
    do_exec(bridge, "raise RuntimeError('boom')")
    after = vtk_mod.vtkOutputWindow.GetInstance()
    assert after is before


def test_vtk_output_window_restored_after_success(bridge, fake_paraview):
    vtk_mod = fake_paraview["vtk"]
    before = vtk_mod.vtkOutputWindow.GetInstance()
    do_exec(bridge, "1 + 1")
    after = vtk_mod.vtkOutputWindow.GetInstance()
    assert after is before


# ---- B-15: rendering -------------------------------------------------------

def test_render_called_by_default(bridge, monkeypatch):
    calls = []
    monkeypatch.setattr(fake_simple, "Render", lambda: calls.append(1))
    do_exec(bridge, "1")
    assert calls == [1]


def test_render_not_called_when_render_false(bridge, monkeypatch):
    calls = []
    monkeypatch.setattr(fake_simple, "Render", lambda: calls.append(1))
    do_exec(bridge, "1", render=False)
    assert calls == []


def test_render_exception_does_not_corrupt_result(bridge, monkeypatch):
    def boom():
        raise RuntimeError("render blew up")
    monkeypatch.setattr(fake_simple, "Render", boom)
    status, fields = do_exec(bridge, "1 + 1")
    assert status == "ok"
    assert fields["value"] == "2"


# ---- B-17: state summary ---------------------------------------------------

def test_state_attached_to_every_exec_response(bridge):
    req = {"v": 1, "id": "t1", "op": "exec", "code": "1"}
    resp = _full_response(bridge, req)
    assert resp["state"] is not None
    assert "sources" in resp["state"]


def test_state_is_null_when_get_sources_raises(bridge, monkeypatch):
    def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(fake_simple, "GetSources", boom)
    req = {"v": 1, "id": "t1", "op": "exec", "code": "1"}
    resp = _full_response(bridge, req)
    assert resp["state"] is None
    # the exec itself must still have succeeded despite the state failure
    assert resp["status"] == "ok"


def test_state_visible_detection_does_not_call_get_representation(bridge):
    do_exec(bridge, "Sphere()\nShow()\n1")
    req = {"v": 1, "id": "t1", "op": "exec", "code": "1"}
    resp = _full_response(bridge, req)
    assert fake_simple._get_representation_calls == []
    source = resp["state"]["sources"][0]
    assert source["visible"] is True


def test_state_truncates_sources_over_50(bridge):
    do_exec(bridge, "for _ in range(51): Sphere()\n'done'")
    req = {"v": 1, "id": "t1", "op": "exec", "code": "1"}
    resp = _full_response(bridge, req)
    assert len(resp["state"]["sources"]) == 50
    assert resp["state"]["truncated"] is True
    assert resp["state"]["count"] == 51


def _full_response(bridge, req):
    """Build a full response envelope the way _process_line does, without
    going through a socket."""
    status, fields = bridge._dispatch(req)
    resp = {"v": 1, "id": req.get("id"), "status": status}
    resp.update(fields)
    bridge._attach_state(resp)
    return resp
