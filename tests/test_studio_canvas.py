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
    assert out["d"] == {"state": None, "files": [], "errors": []}   # not in this run: it is not waiting for it
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
    assert out["model"] == ["split", "repaint", "remesh", "unwrap", "combine", "saveas", "finish", "rig", "export"]
    assert out["picture"] == ["back", "parts", "views", "model", "cutout"]
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
                # what it does first; an engine's own name may follow, never its setup notes
                setup_notes = ("C++", "GGML", "Xiong", "dgrauet") + (("(",) if "engine" in key else ())
                assert label and not any(w in str(label) for w in setup_notes), (box, key, label)
                if value != "":
                    assert ga.clean_settings(box, {key: value}) != {}, (box, key, value)
    assert dict(fields["model"])["engine"] == [["pixal3d", "Best detail · Pixal3D"], ["newthing", "New"]]
    # the catalog's picture engine ids, named by what they do; one not installed is not offered
    assert dict(fields["picture"])["picture_engine"] == [["", "Pick for me: Follows long descriptions · Qwen-Image 2.1"], ["qwen", "Follows long descriptions · Qwen-Image 2.1"]]


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


def test_picked_out_parts_each_get_a_tile_and_all_carry_on():
    out = _run("""let g = m.newGraph('B');
      for (const t of ['picture', 'parts', 'model']) g = m.addNode(g, t, 0, 0);
      g = m.connect(m.connect(g, 'n1', 'n2'), 'n2', 'n3');
      const parts = [{ file: 'images/helmet__part__1/a.png', label: 'helmet' }, { file: 'images/hair__part__2/b.png', label: 'hair' }];
      console.log(JSON.stringify({ choice: ['n1', 'n2', 'n3'].map((id) => m.isChoice(g, id)),
        tiles: m.optionTiles(g, g.nodes[1], parts).map((t) => t.label), face: m.tileFace(g, g.nodes[1], { files: parts }),
        models: m.tileFace(g, g.nodes[2], { files: [{ file: 'a.glb' }, { file: 'b.glb' }] }).kind }))""")
    assert out["choice"] == [True, False, False]          # the parts are not alternatives: each carries on
    assert out["tiles"] == ["helmet", "hair"]             # but each is its own tile to carry on from alone
    assert out["face"]["kind"] == "empty" and out["face"]["text"].startswith("2 parts")
    assert out["models"] == "mosaic"                      # a 3D model of each part, side by side


def test_an_engine_is_named_by_what_it_does_then_by_its_own_name_with_a_note():
    out = _settings("""console.log(JSON.stringify([m.modelName('Hunyuan3D-MLX (Xiong, full pipeline)'), m.modelName('Stable Fast 3D'),
      m.modelName('FLUX.2 [klein] 4B'), m.engineNote('engine', 'sf3d'), m.engineNote('engine', 'mystery'),
      m.engineNote('picture_engine', ''), m.engineNote('picture_engine', 'qwen')]))""")
    assert out[:3] == ["Hunyuan3D-MLX", "Stable Fast 3D", "FLUX.2 [klein] 4B"]
    assert out[3].startswith("A quick look in seconds") and out[4] == ""
    assert out[5] == "The engine Create would use." and out[6].startswith("Slower, but sticks closely")


PICTURE_ENGINES = """[{ id: 'flux', label: 'FLUX.2 [klein] 4B', suggested: true, installed: true, can_draw_backs: true },
  { id: 'flux-light', label: 'FLUX.2 [klein] 4B Light', installed: false, can_draw_backs: false },
  { id: 'qwen', label: 'Qwen-Image 2.1', installed: true, can_draw_backs: true },
  { id: 'old', label: 'Old Draw', installed: true, can_draw_backs: false }]"""


def test_drawn_by_names_the_engine_it_would_pick_and_redrawing_boxes_get_one_too():
    out = _settings(f"""const tools = {{ pictureEngines: {PICTURE_ENGINES} }};
      const menu = (type) => m.FIELDS[type].find((f) => f[0] === 'picture_engine')[4](tools);
      console.log(JSON.stringify({{ picture: menu('picture'), parts: menu('parts'), back: menu('back'), views: menu('views'),
        notes: [m.engineNote('picture_engine', '', 'picture', tools), m.engineNote('picture_engine', '', 'parts', tools),
                m.engineNote('picture_engine', '', 'picture', {{ pictureEngines: [] }})] }}))""")
    assert out["picture"][0] == ["", "Pick for me: Quick pictures · FLUX.2 [klein] 4B"]
    assert [c[0] for c in out["picture"]] == ["", "flux", "qwen", "old"]           # only what is set up
    assert out["parts"] == out["back"] == out["views"] == [
        ["", "Pick for me: the one that drew the picture"], ["flux", "Quick pictures · FLUX.2 [klein] 4B"],
        ["qwen", "Follows long descriptions · Qwen-Image 2.1"]]                     # only those that can redraw
    assert out["notes"][0] == "Uses FLUX.2 [klein] 4B: the first one set up, as Create does."
    assert out["notes"][1] == "Uses the engine that drew the picture, so they match."
    assert out["notes"][2] == "The engine Create would use."


def test_four_views_runs_three_steps_on_one_box_and_its_3d_model_gets_the_four_view_engine():
    out = _run("""let g = m.newGraph('B');
      for (const t of ['picture', 'views', 'model']) g = m.addNode(g, t, 0, 0);
      g = m.connect(m.connect(g, 'n1', 'n2'), 'n2', 'n3');
      const run = { status: 'running', stage: 0, stages: [{ nodes: ['n1', 'n2', 'n3'], steps: ['picture', 'left', 'back', 'right', 'model'] }] };
      console.log(JSON.stringify({ busy: ['left', 'right', 'model'].map((current) => m.runningNode(run, g, { current })),
        choice: m.isChoice(g, 'n2'), sides: m.tileFace(g, g.nodes[1], { files: [{ file: 'l.png' }, { file: 'b.png' }, { file: 'r.png' }] }).kind }))""")
    assert out == {"busy": ["n2", "n2", "n3"], "choice": False, "sides": "mosaic"}
    picked = _settings("""console.log(JSON.stringify([m.defaultsFor('model', { engines: [{ id: 'pixal3d' }] }, 'views'),
      m.defaultsFor('model', { engines: [{ id: 'pixal3d' }] }, 'picture')]))""")
    assert picked == [{"engine": "hunyuan_xiong"}, {"engine": "pixal3d"}]


def test_a_try_is_fixed_by_saying_what_is_wrong_or_made_again():
    out = _run("""const g = { nodes: [{ id: 'p', type: 'picture', settings: { prompt: 'x' } },
        { id: 'g', type: 'picture', settings: { file: 'images/k.png' } }, { id: 'm', type: 'model', settings: {} },
        { id: 'c', type: 'cutout', settings: {} }, { id: 'r', type: 'remesh', settings: {} }], edges: [] };
      console.log(JSON.stringify([m.redoChoices(g, 'p', 'images/a.png'), m.redoChoices(g, 'g', 'images/k.png'),
        m.redoChoices(g, 'm', 'x/m.glb'), m.redoChoices(g, 'c', 'images/c.png'), m.redoChoices(g, 'r', 'x/r.glb')]))""")
    assert out == [{"note": True, "again": True}, {"note": False, "again": False}, {"note": False, "again": True},
                   {"note": True, "again": False}, {"note": False, "again": False}]
    assert ga.REMAKE == set(_run("console.log(JSON.stringify(m.REMAKE))"))


def test_a_fixed_try_takes_the_old_ones_place_and_a_failed_one_leaves_it():
    out = _run("""const before = { x: [{ status: 'done', file: 'a.png', label: 'helmet' }, { status: 'done', file: 'b.png', label: 'cape' }],
                                   m: [{ status: 'done', file: 'm.glb', label: 'cape' }] };
      const g = { nodes: [{ id: 'x', type: 'parts', settings: {}, picked: 'b.png' }, { id: 'm', type: 'model', settings: {} }],
                  edges: [{ from: 'x', to: 'm', file: 'b.png' }], last_run: { results: before } };
      const redo = { node: 'x', file: 'b.png' };
      const done = m.finishRun(g, { status: 'done', redo, results: { x: [{ status: 'done', file: 'c.png' }] } });
      const failed = m.mergeRun(before, { status: 'error', redo, results: { x: [{ status: 'error', error: 'no' }] } });
      const again = m.mergeRun(before, { again: true, results: { m: [{ status: 'done', file: 'n.glb' }] } });
      console.log(JSON.stringify({ done, failed, again }))""")
    assert out["done"]["last_run"]["results"]["x"][1] == {"status": "done", "file": "c.png", "label": "cape", "was": ["b.png"]}
    assert out["done"]["last_run"]["results"]["m"] == [{"status": "done", "file": "m.glb", "label": "cape"}]
    assert out["done"]["edges"][0]["file"] == "c.png" and out["done"]["nodes"][0]["picked"] == "c.png"
    assert out["failed"]["x"][1]["file"] == "b.png"
    assert [r["file"] for r in out["again"]["m"]] == ["m.glb", "n.glb"]


def test_a_run_says_where_it_is_and_what_it_is_doing():
    out = _run("""const run = { id: 'r', graph: 'g1', name: 'Knight parts', status: 'running', stage: 1, elapsed: 83,
        plan: ['Starts from Picture you gave', 'Run 1, once for each of 3 parts: 3D model'] };
      console.log(JSON.stringify([m.runBanner(run, 'g1'), m.runBanner(run, 'g2'), m.runBanner(null, 'g1'),
        m.runBanner({ ...run, status: 'done' }, 'g2'), m.runBanner({ ...run, graph: 'g1', name: '' }, 'g2')]))""")
    assert out[0] == {"here": True, "text": "Running: 3D model, once for each of 3 parts · 1 min 23 s"}
    assert out[1] == {"here": False, "text": "Running on “Knight parts”: 3D model, once for each of 3 parts · 1 min 23 s"}
    assert out[2] is None and out[3] is None
    assert out[4]["text"].startswith("Running on another canvas:")


def test_lines_start_from_the_tiles_that_carry_on():
    out = _run("""const g = { nodes: [{ id: 'x', type: 'parts', x: 0, y: 0, settings: {} }, { id: 'v', type: 'views', x: 0, y: 400, settings: {} },
        { id: 'p', type: 'picture', x: 0, y: 800, settings: {} }, { id: 'm', type: 'model', x: 600, y: 0, settings: {} }], edges: [] };
      const tiles = [{ file: 'a.png', port: { x: 300, y: 10 } }, { file: 'b.png', port: { x: 300, y: 120 } }];
      const port = (n) => ({ x: n.x + 1, y: n.y + 2 });
      console.log(JSON.stringify([
        m.lineStarts(g, { from: 'x', to: 'm' }, tiles, port),               // every part goes on
        m.lineStarts(g, { from: 'x', to: 'm', file: 'b.png' }, tiles, port), // one part dragged onto the line
        m.lineStarts(g, { from: 'v', to: 'm' }, tiles, port),               // every side goes on
        m.lineStarts(g, { from: 'p', to: 'm' }, tiles, port),               // options: the box's pick goes on
        m.lineStarts(g, { from: 'x', to: 'm' }, [], port)]))""")
    assert out == [[{"x": 300, "y": 10}, {"x": 300, "y": 120}], [{"x": 300, "y": 120}], [{"x": 300, "y": 10}, {"x": 300, "y": 120}],
                   [{"x": 1, "y": 802}], [{"x": 1, "y": 2}]]


def test_a_box_waits_only_for_a_run_that_will_reach_it_and_says_when_it_is_queued():
    out = _run("""const g = { nodes: ['a', 'b', 'c', 'd'].map((id) => ({ id, type: 'model', settings: {} })),
        edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }] };
      const run = { status: 'running', stages: [{ nodes: ['a', 'b'] }], results: {} };
      const queued = [{ graph: 'g1', queued_for: 'c', mode: 'only', position: 2 }, { graph: 'g1', queued_for: 'b', mode: 'from', position: 1 },
                      { graph: 'g2', queued_for: 'd', mode: 'only', position: 3 }];
      const all = [{ graph: 'g1', queued_for: null, mode: null, position: 1 }];
      console.log(JSON.stringify({ states: ['a', 'c', 'd'].map((id) => m.nodeState(run, id).state),
        queued: Object.fromEntries(m.queuedBoxes(queued, g, 'g1')), all: [...m.queuedBoxes(all, g, 'g1').keys()],
        banner: m.runBanner({ ...run, graph: 'g1', name: 'X', plan: ['Run 1: 3D model'], stage: 0, elapsed: 5 }, 'g1', 2).text }))""")
    assert out["states"] == ["waiting", None, None]          # only boxes in the run wait; the rest are idle
    assert out["queued"] == {"b": 1, "c": 1}                  # from b: b and below; c's best place is 1st
    assert out["all"] == ["a", "b", "c", "d"]
    assert out["banner"] == "Running: 3D model · 5 s · 2 queued"


def test_the_running_line_names_a_side_run_beside_the_main_one():
    out = _run("""const main = { id: 'a', graph: 'g1', name: 'Knight', status: 'running', stage: 0, elapsed: 70, plan: ['Run 1: 3D model'] };
      const side = { id: 'b', graph: 'g2', name: 'Tools', status: 'running', stage: 0, elapsed: 4, plan: ['Run 1: Quad remesh → Save as'] };
      console.log(JSON.stringify([m.runBanner(main, 'g1', 0, side).text, m.runBanner(null, 'g2', 1, side),
        m.runBanner(main, 'g1', 0, { ...side, graph: 'g1' }).text]))""")
    assert out[0] == "Running: 3D model · 1 min 10 s · beside it on “Tools”: Quad remesh → Save as"
    assert out[1] == {"here": True, "text": "Running: Quad remesh → Save as · 4 s · 1 queued"}
    assert out[2].endswith("· beside it: Quad remesh → Save as")
