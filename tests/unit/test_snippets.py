"""Validates src/paraview_mcp/snippets.py's canned exec() strings against
the bridge's real exec engine + fake paraview (docs/M2_PLAN.md 4, S-11).

This is a stronger check than test_server_tools.py's: that file mocks
bridge_client entirely, so it verifies the server *sends* the right
snippet but never actually runs it. Here the snippet text really goes
through bridge._dispatch()/_run_exec() -- the same path a real ParaView
process would run it through. GET_STATE_ARRAYS/FULL/RESET_PIPELINE were
additionally hand-verified against real ParaView 6.1.1 while writing them
(docs/M2_PLAN.md 1.4-3); these tests catch regressions against the fakes.
"""
import ast
import json

from paraview_mcp import snippets
from tests.fakes.paraview import simple as fake_simple
from tests.unit.test_bridge_exec import do_exec

ALL_SNIPPETS = {
    "GET_SCREENSHOT": snippets.GET_SCREENSHOT,
    "GET_STATE_ARRAYS": snippets.GET_STATE_ARRAYS,
    "GET_STATE_FULL": snippets.GET_STATE_FULL,
    "RESET_PIPELINE": snippets.RESET_PIPELINE,
}


# ---- S-11: snippets only bind __-prefixed names in the shared namespace ----

def _assigned_names(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.append(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.alias):
            names.append(node.asname or node.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.append(node.name)
    return names


def test_snippets_only_bind_dunder_prefixed_names():
    for label, code in ALL_SNIPPETS.items():
        bad = [n for n in _assigned_names(ast.parse(code)) if not n.startswith("__")]
        assert not bad, "%s binds non-__-prefixed name(s): %r" % (label, bad)


# ---- get_state(detail="arrays"/"full") snippets ----------------------------

def test_get_state_arrays_snippet_runs_against_fake_paraview(bridge):
    sphere = fake_simple.Sphere()
    fake_simple.set_data_information(
        sphere, point_arrays=[("Normals", [(-1.0, 1.0)] * 3)], n_points=50, n_cells=96)

    status, fields = do_exec(bridge, snippets.GET_STATE_ARRAYS)

    assert status == "ok"
    value = json.loads(fields["value"])
    arr = value["arrays"]["Sphere1"]
    assert arr["point_arrays"] == [{"name": "Normals", "n_components": 3,
                                     "ranges": [[-1.0, 1.0], [-1.0, 1.0], [-1.0, 1.0]]}]
    assert arr["cell_arrays"] == []


def test_get_state_arrays_snippet_reports_per_source_errors_without_failing(bridge):
    fake_simple.Sphere()

    def _boom(*a, **kw):
        raise RuntimeError("no data")
    fake_simple._active_source.GetDataInformation = _boom

    status, fields = do_exec(bridge, snippets.GET_STATE_ARRAYS)

    assert status == "ok"  # one bad source must not fail the whole request
    value = json.loads(fields["value"])
    assert "error" in value["arrays"]["Sphere1"]


def test_get_state_full_snippet_includes_bounds_counts_and_properties(bridge):
    sphere = fake_simple.Sphere(Radius=2.0)
    fake_simple.set_data_information(sphere, n_points=50, n_cells=96,
                                      bounds=(-2.0, 2.0, -2.0, 2.0, -2.0, 2.0))

    status, fields = do_exec(bridge, snippets.GET_STATE_FULL)

    assert status == "ok"
    src = json.loads(fields["value"])["full"]["Sphere1"]
    assert src["n_points"] == 50
    assert src["n_cells"] == 96
    assert src["bounds"] == [-2.0, 2.0, -2.0, 2.0, -2.0, 2.0]
    assert src["properties"]["Radius"] == 2.0


def test_get_state_full_snippet_skips_non_scalar_properties(bridge):
    # FakeUnserializableProxy-like: a property whose value isn't a JSON
    # scalar or a sequence of scalars must be silently dropped, not raise.
    sphere = fake_simple.Sphere()
    sphere.weird_prop = fake_simple.FakeProxy("Weird")

    status, fields = do_exec(bridge, snippets.GET_STATE_FULL)

    assert status == "ok"
    props = json.loads(fields["value"])["full"]["Sphere1"]["properties"]
    assert "weird_prop" not in props


def test_get_state_arrays_and_full_cover_every_source(bridge):
    fake_simple.Sphere()
    fake_simple.Cone()

    status, fields = do_exec(bridge, snippets.GET_STATE_ARRAYS)

    assert status == "ok"
    assert set(json.loads(fields["value"])["arrays"].keys()) == {"Sphere1", "Cone1"}


# ---- reset_session's pipeline-clear snippet ---------------------------------

def test_reset_pipeline_snippet_deletes_all_sources_any_order(bridge):
    fake_simple.Sphere()
    fake_simple.Cone()

    status, fields = do_exec(bridge, snippets.RESET_PIPELINE)

    assert status == "ok"
    value = json.loads(fields["value"])
    assert set(value["deleted"]) == {"Sphere1", "Cone1"}
    assert fake_simple.GetSources() == {}


def test_reset_pipeline_snippet_on_empty_pipeline_is_a_noop(bridge):
    status, fields = do_exec(bridge, snippets.RESET_PIPELINE)
    assert status == "ok"
    assert json.loads(fields["value"]) == {"deleted": []}
