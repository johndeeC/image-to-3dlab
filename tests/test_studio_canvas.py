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
    # a board pans and zooms, so a box may sit left of or above where it started
    assert [(n["id"], n["type"], n["x"], n["y"]) for n in g["nodes"]] == [("n2", "model", 300, 20), ("n3", "finish", -50, 9)]
    assert g["nodes"][0]["settings"] == {"engine": "sf3d"} and g["schema"] == 2 and g["id"] is None


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
    assert out[0] == "The lines would go round in a loop." or "Finish makes a game-ready model" in out[0]
    assert out[1] is True


def test_a_line_is_a_curve_from_one_box_to_the_next():
    path = _run("console.log(JSON.stringify(m.edgePath({ x: 0, y: 0 }, { x: 400, y: 100 })))")
    w, h = _run("console.log(JSON.stringify([m.NODE_W, m.PORT_Y]))")
    assert path.startswith(f"M {w} {h} C ") and path.endswith(f"400 {100 + h}")


def test_the_starters_are_graphs_the_server_would_run():
    for kind in ("character", "sheet", "compare", "parts"):
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
      run.results.b = [{ status: 'waiting' }];
      const graph = { nodes: [{ id: 'a', type: 'picture' }, { id: 'b', type: 'model' }, { id: 'c', type: 'rig' }, { id: 'd', type: 'move' }] };
      console.log(JSON.stringify({ b: m.nodeState(run, 'b').state, a: m.nodeState(run, 'a'), c: m.nodeState(run, 'c'), d: m.nodeState(run, 'd'), none: m.nodeState(null, 'a'),
        running: m.runningNode(run, graph, { current: 'rigged' }), idle: m.runningNode(run, graph, null) }))""")
    assert out["a"] == {"state": "done", "files": [{"file": "p.png", "label": None}], "errors": []}
    assert out["b"] == "waiting"                                # below a Picture whose options wait for a pick
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


def test_a_line_dropped_on_the_board_offers_only_the_boxes_that_take_it():
    out = _run("""console.log(JSON.stringify({ model: m.takersOf('model'), picture: m.takersOf('picture'),
      export: m.takersOf('export'), back: m.takersOf('back') }))""")
    assert out["model"] == ["split", "finish", "rig", "export"]
    assert out["picture"] == ["back", "model"]
    assert out["export"] == [] and out["back"] == ["model"]


def test_a_box_added_from_a_dropped_line_comes_already_joined():
    g = _run("""let g = m.addNode(m.newGraph('B'), 'picture', 0, 0);
      g = m.addConnected(g, 'n1', 'model', 300, 0, { engine: 'sf3d' });
      console.log(JSON.stringify(g))""")
    assert g["nodes"][1]["settings"] == {"engine": "sf3d"} and g["edges"] == [{"from": "n1", "to": "n2"}]


def test_moving_and_removing_several_boxes_never_changes_the_graph_it_was_given():
    out = _run("""let g = m.newGraph('B');
      for (const t of ['picture', 'model', 'finish']) g = m.addNode(g, t, 0, 0);
      g = m.connect(m.connect(g, 'n1', 'n2'), 'n2', 'n3');
      g = m.group(g, ['n2', 'n3'], 'Body');
      const moved = m.moveNodes(g, ['n1', 'n2'], 10, -5);
      const gone = m.removeNodes(g, ['n2', 'n3']);
      console.log(JSON.stringify({ old: g.nodes.map((n) => [n.x, n.y]), moved: moved.nodes.map((n) => [n.x, n.y]),
        edges: gone.edges, groups: gone.groups }))""")
    assert out["old"] == [[0, 0], [0, 0], [0, 0]]
    assert out["moved"] == [[10, -5], [10, -5], [0, 0]]
    assert out["edges"] == [] and out["groups"] == []        # an emptied frame goes with its boxes


def test_join_selected_links_left_to_right_where_it_fits():
    out = _run("""let g = m.newGraph('B');
      g = m.addNode(g, 'finish', 600, 0); g = m.addNode(g, 'picture', 0, 0); g = m.addNode(g, 'model', 300, 0); g = m.addNode(g, 'move', 900, 0);
      const { graph, made } = m.connectSelected(g, ['n1', 'n2', 'n3', 'n4']);
      console.log(JSON.stringify({ edges: graph.edges, made }))""")
    assert out["edges"] == [{"from": "n2", "to": "n3"}, {"from": "n3", "to": "n1"}] and out["made"] == 2   # Move takes no Finish


def test_frames_hold_each_box_once_and_come_off_cleanly():
    out = _run("""let g = m.newGraph('B');
      for (const t of ['picture', 'model', 'finish']) g = m.addNode(g, t, 0, 0);
      g = m.group(g, ['n1', 'n2'], 'Front'); g = m.group(g, ['n2', 'n3'], 'Back');
      const framed = g.groups;
      const bounds = m.groupBounds(g.groups[1], [{ id: 'n2', x: 100, y: 100 }, { id: 'n3', x: 400, y: 50 }], { n2: { w: 200, h: 100 }, n3: { w: 210, h: 150 } }, 10);
      g = m.renameGroup(g, 'g2', '  Back half ');
      const named = g.groups[1].label;
      g = m.ungroup(g, ['n1']);
      console.log(JSON.stringify({ framed, bounds, named, left: g.groups }))""")
    assert out["framed"] == [{"id": "g1", "label": "Front", "nodes": ["n1"]}, {"id": "g2", "label": "Back", "nodes": ["n2", "n3"]}]
    assert out["bounds"] == {"x": 90, "y": 20, "w": 530, "h": 190}
    assert out["named"] == "Back half"
    assert out["left"] == [{"id": "g2", "label": "Back half", "nodes": ["n2", "n3"]}]


def test_up_and_down_the_lines():
    out = _run("""let g = m.newGraph('B');
      for (const t of ['picture', 'model', 'finish', 'rig']) g = m.addNode(g, t, 0, 0);
      g = m.connect(m.connect(m.connect(g, 'n1', 'n2'), 'n2', 'n3'), 'n2', 'n4');
      g = m.pickResult(g, 'n1', 'a.png');
      console.log(JSON.stringify({ up: m.upstreamOf(g, 'n3'), down: m.downstreamOf(g, 'n1'), picked: g.nodes[0].picked,
        unpicked: m.pickResult(g, 'n1', null).nodes[0].picked ?? null }))""")
    assert out == {"up": ["n2", "n1"], "down": ["n2", "n3", "n4"], "picked": "a.png", "unpicked": None}


def _settings(body: str):
    url = (REPO / "viewer" / "studio" / "canvas-settings.js").as_uri()
    result = subprocess.run([NODE, "--input-type=module", "--eval", f"import * as m from {json.dumps(url)};\n{body}"],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_every_box_setting_on_screen_is_one_the_server_keeps():
    keys = _settings("console.log(JSON.stringify(m.settingKeys()))")
    assert {k: sorted(v) for k, v in keys.items()} == {k: sorted(v) for k, v in ga.SETTINGS.items()}


def test_every_choice_on_screen_is_a_value_the_server_keeps():
    tools = {"engines": [{"id": "pixal3d", "label": "Pixal3D (C++/GGML)"}, {"id": "newthing", "label": "New"}],
             "presets": [{"id": "walk", "label": "Walk"}], "pictureEngines": [{"id": "qwen", "label": "Qwen-Image 2.1 (text to image)", "installed": True},
                                {"id": "flux-light", "label": "FLUX", "installed": False}]}
    fields = _settings(f"""const t = {json.dumps(tools)};
      console.log(JSON.stringify(Object.fromEntries(Object.entries(m.FIELDS).map(([type, fs]) => [type,
        fs.filter((f) => f[3] === 'select').map((f) => [f[0], f[4](t)])]))))""")
    for box, selects in fields.items():
        for key, choices in selects:
            for value, label in choices:
                # named by what it does, never where it came from (CLAUDE.md "Studio text is for the user")
                assert label and not any(w in str(label) for w in ("C++", "GGML", "Xiong", "dgrauet", "Pixal3D")), (box, key, label)
                if value != "":
                    assert ga.clean_settings(box, {key: value}) != {}, (box, key, value)
    assert dict(fields["model"])["engine"] == [["pixal3d", "Best detail"], ["newthing", "New"]]
    # the catalog's picture engine ids, named by what they do; one not installed is not offered
    assert dict(fields["picture"])["picture_engine"] == [["", "The usual one"], ["qwen", "Follows long descriptions"]]


def test_a_form_value_becomes_what_the_box_keeps():
    out = _settings("""const f = (t, k) => m.FIELDS[t].find((x) => x[0] === k);
      console.log(JSON.stringify([m.readField(f('finish', 'texture_size'), '512'), m.readField(f('finish', 'height_m'), '999'),
        m.readField(f('finish', 'height_m'), '1.7'), m.readField(f('finish', 'surface_maps'), false), m.readField(f('picture', 'prompt'), ''),
        m.toggleValue('surface_maps', {}), m.toggleValue('surface_maps', { surface_maps: false }),
        m.defaultsFor('model', { engines: [{ id: 'sf3d' }] }), m.defaultsFor('move', { presets: [{ id: 't', take: true }, { id: 'walk' }] })]))""")
    assert out == [512, "", 1.7, False, "", True, False, {"engine": "sf3d"}, {"move": "walk"}]


def test_a_try_dragged_out_of_a_box_starts_the_next_box_from_it():
    out = _run("""let g = m.newGraph('B');
      for (const t of ['picture', 'model', 'model', 'split', 'rig', 'move']) g = m.addNode(g, t, 0, 0);
      g = m.connect(g, 'n1', 'n2');
      g = m.useResult(g, 'n1', 'images/b.png', 'n2');          // already joined: the line now carries b
      g = m.useResult(g, 'n1', 'images/c.png', 'n3');          // joined by the drag
      const fresh = m.addFromResult(g, 'n2', 'm/2.glb', 'finish', 300, 0);
      let refused = null;
      try { m.useResult(g, 'n1', 'images/a.png', 'n6'); } catch (e) { refused = e.message; }
      g = m.connect(m.connect(g, 'n2', 'n4'), 'n4', 'n5');
      console.log(JSON.stringify({ edges: g.edges, b: m.edgeFile(g, 'n1', 'n2'), none: m.edgeFile(g, 'n2', 'n4'),
        fresh: fresh.edges.at(-1), refused,
        choice: ['n1', 'n2', 'n4', 'n5'].map((id) => m.isChoice(g, id)) }))""")
    assert out["edges"][:2] == [{"from": "n1", "to": "n2", "file": "images/b.png"}, {"from": "n1", "to": "n3", "file": "images/c.png"}]
    assert out["b"] == "images/b.png" and out["none"] is None
    assert out["fresh"] == {"from": "n2", "to": "n7", "file": "m/2.glb"}
    assert out["refused"].startswith("Move takes a rigged model")
    assert out["choice"] == [True, True, False, False]      # a split's props, and the rigs below it, all carry on


def test_a_tile_shows_its_one_result_a_mosaic_of_several_or_its_own_words():
    out = _run("""let g = m.newGraph('B');
      for (const t of ['picture', 'model', 'split', 'rig', 'asset']) g = m.addNode(g, t, 0, 0);
      g = m.connect(m.connect(m.connect(g, 'n1', 'n2'), 'n2', 'n3'), 'n3', 'n4');
      g = m.setSetting(g, 'n1', 'prompt', 'a very long description of a brass oil lantern');
      const st = (...f) => ({ files: f.map((file) => ({ file })) });
      const node = (id) => g.nodes.find((n) => n.id === id);
      const before = m.tileFace(g, node('n1'), st());
      const newest = m.tileFace(g, node('n1'), st('a.png', 'b.png', 'c.png'));
      g = m.pickResult(g, 'n1', 'a.png');
      const picked = m.tileFace(g, node('n1'), st('a.png', 'b.png', 'c.png'));
      const props = m.tileFace(g, node('n3'), st(...'abcdefghijk'.split('').map((x) => x + '.glb')));
      const rigs = m.tileFace(g, node('n4'), st('r1.glb', 'r2.glb')).kind;
      const given = m.tileFace(g, m.setSetting(g, 'n5', 'file', 'm/k.glb').nodes[4], st());
      console.log(JSON.stringify({ before, newest: [newest.file, newest.tryIndex, newest.count], picked: [picked.file, picked.tryIndex],
        props: [props.kind, props.files.length, props.count], rigs, given: [given.kind, given.file],
        tags: [m.tagText(node('n1'), 3), m.tagText(node('n2')), m.tagText(node('n3'), 9)], tones: m.NODES.finish.tone }))""")
    assert out["before"] == {"kind": "empty", "file": None, "files": [], "count": 0, "tryIndex": -1,
                             "text": "a very long description of a brass oil lantern"}
    # tries sit beside the box as their own tiles: the box shows its words, then the one picked
    assert out["newest"] == [None, -1, 3] and out["picked"] == ["a.png", 0]
    assert out["props"] == ["mosaic", 9, 11] and out["rigs"] == "mosaic"      # every prop carries on: a mosaic
    assert out["given"] == ["one", "m/k.glb"]
    assert out["tags"] == ["a very long description o… (3)", "3D model", "Split into props (9)"]
    assert out["tones"] == "finish"


def test_each_try_gets_a_tile_of_its_own_to_carry_on_from():
    out = _run("""let g = m.newGraph('B');
      for (const t of ['picture', 'model', 'finish', 'split']) g = m.addNode(g, t, 0, 0);
      g = m.moveNode(g, 'n1', 40, 60); g = m.moveNode(g, 'n2', 290, 60); g = m.moveNode(g, 'n3', 540, 60);
      g = m.connect(m.connect(g, 'n1', 'n2'), 'n2', 'n3');
      const four = ['a', 'b', 'c', 'd'].map((x, i) => ({ file: x + '.png', label: i === 0 ? 'Option 1' : null }));
      const tiles = m.optionTiles(g, g.nodes[0], four);
      const room = m.makeRoom(g, 'n1', m.OPTION_OFFSET + m.OPTION_W + 60);
      console.log(JSON.stringify({ tiles: tiles.map((t) => [t.file, t.label, t.x, t.y, t.port]),
        one: m.optionTiles(g, g.nodes[0], four.slice(0, 1)), props: m.optionTiles(g, g.nodes[3], four),
        moved: room.nodes.map((n) => n.x), again: m.makeRoom(room, 'n1', 196).nodes.map((n) => n.x),
        curve: m.curve(0, 0, 100, 50) }))""")
    w, gap = 96, 12
    assert out["tiles"][0] == ["a.png", "Option 1", 40 + 160 + 40, 60, {"x": 240 + w, "y": 60 + w / 2}]
    assert [t[1] for t in out["tiles"]] == ["Option 1", "Try 2", "Try 3", "Try 4"]
    assert out["tiles"][3][3] == 60 + 3 * (w + gap)
    assert out["one"] == [] and out["props"] == []            # one result needs no tile; a split's props are a mosaic
    assert out["moved"] == [40, 396, 646, 0]                  # the 3D model and Finish moved right to make room
    assert out["again"] == out["moved"]                       # there is room already: nothing moves
    assert out["curve"] == "M 0 0 C 50 0, 50 50, 100 50"
