"""The canvas's logic (viewer/studio/canvas.js), run under Node against the shipped file."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import graph_api as ga
import pytest

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required to execute browser ES modules")


def _run(body: str):
    url = (REPO / "viewer" / "studio" / "canvas.js").as_uri()
    program = f"import * as m from {json.dumps(url)};\n{body}"
    result = subprocess.run([NODE, "--input-type=module", "--eval", program],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_the_page_and_the_server_agree_on_what_each_node_takes_and_makes():
    nodes = _run("console.log(JSON.stringify(m.NODES))")
    assert {k: v["label"] for k, v in nodes.items()} == ga.LABELS
    assert {k: v["takes"] for k, v in nodes.items()} == {k: list(v) for k, v in ga.TAKES.items()}
    assert {k: v["makes"] for k, v in nodes.items()} == ga.MAKES
    assert {k: v["step"] for k, v in nodes.items() if v["step"]} == ga.STEP


def test_adding_nodes_gives_each_a_new_id_and_a_place():
    g = _run("""let g = m.newGraph('Board');
      g = m.addNode(g, 'picture', 10, 20, { prompt: 'a fox' });
      g = m.addNode(g, 'model', 300, 20, { engine: 'sf3d' });
      g = m.removeNode(g, 'n1');
      g = m.addNode(g, 'finish', -50, 9);
      console.log(JSON.stringify(g))""")
    assert [(n["id"], n["type"], n["x"], n["y"]) for n in g["nodes"]] == [("n2", "model", 300, 20), ("n3", "finish", 0, 9)]
    assert g["nodes"][0]["settings"] == {"engine": "sf3d"} and g["schema"] == 1 and g["id"] is None


def test_a_line_only_joins_nodes_that_fit():
    out = _run("""let g = m.newGraph('B');
      for (const [t, s] of [['picture', {}], ['model', {}], ['move', {}], ['picture', {}], ['split', {}]]) g = m.addNode(g, t, 0, 0, s);
      const said = [m.canConnect(g, 'n1', 'n2'), m.canConnect(g, 'n1', 'n3'), m.canConnect(g, 'n2', 'n1'),
                    m.canConnect(g, 'n2', 'n2'), m.canConnect(g, 'n2', 'n5')];
      g = m.connect(g, 'n1', 'n2');
      said.push(m.canConnect(g, 'n4', 'n2'), m.canConnect(g, 'n1', 'n2'));
      g = m.connect(g, 'n2', 'n5');
      g = m.removeNode(g, 'n5');
      console.log(JSON.stringify({ said, edges: g.edges }))""")
    ok, move, into_picture, itself, split, second_in, again = out["said"]
    assert ok is True and split is True
    assert move == "Move takes a rigged model, and Picture makes a picture."
    assert into_picture == "Picture takes no line in: it starts a run."
    assert itself == "A node cannot feed itself."
    assert second_in == "3D model already has a line in. Each node takes one."
    assert again == "These two are already joined."
    assert out["edges"] == [{"from": "n1", "to": "n2"}]       # the split's line went with it


def test_a_line_that_would_loop_back_is_refused():
    out = _run("""let g = m.newGraph('B');
      g = m.addNode(g, 'model', 0, 0); g = m.addNode(g, 'finish', 0, 0); g = m.addNode(g, 'rig', 0, 0);
      g = m.connect(g, 'n1', 'n2');
      console.log(JSON.stringify([m.canConnect(g, 'n2', 'n1'), m.canConnect(g, 'n1', 'n3')]))""")
    assert out[0] in ("The lines would go round in a loop.", "3D model takes a picture, and Finish makes a game-ready model.")
    assert out[1] is True


def test_a_line_is_a_curve_from_one_box_to_the_next():
    path = _run("console.log(JSON.stringify(m.edgePath({ x: 0, y: 0 }, { x: 400, y: 100 })))")
    w, h = _run("console.log(JSON.stringify([m.NODE_W, m.PORT_Y]))")
    assert path.startswith(f"M {w} {h} C ") and path.endswith(f"400 {100 + h}")


def test_the_starters_are_graphs_the_server_would_run():
    for kind in ("character", "sheet", "compare"):
        g = _run(f"console.log(JSON.stringify(m.starter('{kind}', {{ engines: ['pixal3d', 'sf3d'], move: 'walk' }})))")
        g["nodes"][0]["settings"]["prompt"] = "a rune knight"
        plan = ga.plan_graph(g, {"goals": {}})
        assert plan.stages, kind
    sheet = _run("console.log(JSON.stringify(m.starter('sheet', { engines: ['sf3d'] })))")
    assert [n["type"] for n in sheet["nodes"]] == ["picture", "model", "split", "export"]


def test_what_a_node_shows_after_a_run():
    out = _run("""const run = { status: 'running', stage: 1,
        stages: [{ nodes: ['a', 'b'], steps: ['picture', 'model'] }, { nodes: ['c'], steps: ['rigged'] }],
        results: { a: [{ status: 'done', file: 'p.png' }], c: [{ status: 'done', file: 'r1.glb', label: 'axe' }, { status: 'error', error: 'it broke', label: 'sword' }] } };
      const graph = { nodes: [{ id: 'a', type: 'picture' }, { id: 'b', type: 'model' }, { id: 'c', type: 'rig' }, { id: 'd', type: 'move' }] };
      console.log(JSON.stringify({ a: m.nodeState(run, 'a'), c: m.nodeState(run, 'c'), d: m.nodeState(run, 'd'), none: m.nodeState(null, 'a'),
        running: m.runningNode(run, graph, { current: 'rigged' }), idle: m.runningNode(run, graph, null) }))""")
    assert out["a"] == {"state": "done", "files": [{"file": "p.png", "label": None}], "errors": []}
    assert out["c"]["state"] == "error" and out["c"]["files"] == [{"file": "r1.glb", "label": "axe"}]
    assert out["c"]["errors"] == ["sword: it broke"]
    assert out["d"] == {"state": "waiting", "files": [], "errors": []}
    assert out["none"] == {"state": None, "files": [], "errors": []}
    assert out["running"] == "c" and out["idle"] is None


def test_a_setting_change_keeps_the_rest_and_an_empty_one_is_dropped():
    g = _run("""let g = m.addNode(m.newGraph('B'), 'picture', 0, 0, { prompt: 'x', goal: 'set' });
      g = m.setSetting(g, 'n1', 'prompt', 'a fox'); g = m.setSetting(g, 'n1', 'goal', '');
      console.log(JSON.stringify(g.nodes[0].settings))""")
    assert g == {"prompt": "a fox"}
