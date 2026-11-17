"""The canvas's view, selection, undo, clipboard, layout and keys (viewer/studio/canvas-*.js), run under
Node against the shipped files."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required to execute browser ES modules")


def _run(module: str, body: str):
    url = (REPO / "viewer" / "studio" / f"{module}.js").as_uri()
    program = f"import * as m from {json.dumps(url)};\n{body}"
    result = subprocess.run([NODE, "--input-type=module", "--eval", program],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def node(id_, x=0, y=0, type_="model"):
    return {"id": id_, "type": type_, "x": x, "y": y, "settings": {}}


# ---------------------------------------------------------------- canvas-view.js
def test_zooming_keeps_the_point_under_the_cursor_still():
    out = _run("canvas-view", """const v = { x: 30, y: -10, k: 1 };
      const before = m.screenToBoard(v, 200, 150);
      const z = m.zoomAt(v, 1.7, 200, 150);
      console.log(JSON.stringify({ before, after: m.screenToBoard(z, 200, 150), k: z.k,
        back: m.boardToScreen(z, before.x, before.y), min: m.zoomAt(v, 0.0001, 0, 0).k, max: m.zoomAt(v, 999, 0, 0).k,
        pct: m.zoomPercent(z), step: m.zoomStep({ x: 0, y: 0, k: 1 }, 1, 800, 600).k }))""")
    assert out["after"] == pytest.approx(out["before"])
    assert out["back"] == pytest.approx({"x": 200, "y": 150})
    assert out["k"] == pytest.approx(1.7) and out["pct"] == "170%"
    assert (out["min"], out["max"], out["step"]) == (0.2, 2.5, pytest.approx(1.2))


def test_fit_puts_every_box_on_a_narrow_screen_and_never_blows_them_up():
    out = _run("canvas-view", """const nodes = [0, 1, 2, 3, 4].map((i) => ({ id: 'n' + i, x: 40 + i * 250, y: 60 }));
      const v = m.fitAll(nodes, {}, 800, 500);
      const right = m.boardToScreen(v, 40 + 4 * 250 + 210, 60);
      const left = m.boardToScreen(v, 40, 60);
      console.log(JSON.stringify({ k: v.k, left, right, one: m.fitAll([{ id: 'a', x: 500, y: 500 }], {}, 800, 500).k,
        empty: m.fitAll([], {}, 800, 500) }))""")
    assert out["k"] < 1
    assert out["left"]["x"] >= 39 and out["right"]["x"] <= 761
    assert out["one"] == 1 and out["empty"] == {"x": 0, "y": 0, "k": 1}


def test_a_pinch_or_wheel_down_zooms_out_and_up_zooms_in():
    out = _run("canvas-view", "console.log(JSON.stringify([m.wheelFactor(100), m.wheelFactor(-100), m.wheelFactor(3, 1)]))")
    assert out[0] < 1 < out[1] and out[2] < 1


# ---------------------------------------------------------------- canvas-select.js
def test_a_dragged_rectangle_picks_the_boxes_it_touches_either_way_it_is_drawn():
    out = _run("canvas-select", """const nodes = [{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 300, y: 0 }, { id: 'c', x: 0, y: 400 }];
      console.log(JSON.stringify([m.nodesInRect(nodes, {}, { x: 250, y: 100, w: -100, h: -50 }),
        m.nodesInRect(nodes, {}, { x: -10, y: -10, w: 600, h: 200 }), m.hitNode(nodes, {}, { x: 310, y: 20 }),
        m.hitNode(nodes, {}, { x: 260, y: 20 }), m.toggle(['a'], 'b', true), m.toggle(['a', 'b'], 'b', true),
        m.toggle(['a', 'b'], 'c', false), m.addAll(['a'], ['b', 'a'], true), m.keepExisting(['a', 'z'], { nodes })]))""")
    assert out == [["a"], ["a", "b"], "b", None, ["a", "b"], ["a"], ["c"], ["a", "b"], ["a"]]


# ---------------------------------------------------------------- canvas-history.js
def test_undo_and_redo_walk_back_and_forth_and_one_drag_is_one_step():
    out = _run("canvas-history", """let h = m.createHistory();
      h = m.record(h, 'g0', 'move:a', 1000); h = m.record(h, 'g0b', 'move:a', 1100); h = m.record(h, 'g0c', 'move:a', 1200);
      h = m.record(h, 'g1', null, 5000);
      const u1 = m.undo(h, 'g2'); const u2 = m.undo(u1.history, u1.graph); const r1 = m.redo(u2.history, u2.graph);
      const fresh = m.record(u2.history, 'x', null, 9000);
      console.log(JSON.stringify({ past: h.past, u1: u1.graph, u2: u2.graph, r1: r1.graph, none: m.undo(u2.history, 'g0'),
        cleared: fresh.future, noRedo: m.redo(m.createHistory(), 'g') }))""")
    assert out["past"] == ["g0", "g1"]           # three moves of one drag were one step
    assert (out["u1"], out["u2"], out["r1"]) == ("g1", "g0", "g1")
    assert out["none"] is None and out["cleared"] == [] and out["noRedo"] is None


# ---------------------------------------------------------------- canvas-clip.js
def test_paste_gives_new_ids_keeps_the_inner_lines_and_drops_results():
    out = _run("canvas-clip", """const g = { nodes: [{ id: 'n1', type: 'picture', x: 0, y: 0, settings: { prompt: 'a fox' }, picked: 'p.png' },
        { id: 'n2', type: 'model', x: 250, y: 0, settings: { engine: 'sf3d' } }, { id: 'n3', type: 'finish', x: 500, y: 0, settings: {} }],
        edges: [{ from: 'n1', to: 'n2' }, { from: 'n2', to: 'n3' }], groups: [] };
      const clip = m.copyNodes(g, ['n1', 'n2']);
      const { graph, ids } = m.pasteNodes(g, clip, 10, 20);
      console.log(JSON.stringify({ clip, ids, added: graph.nodes.slice(3), edges: graph.edges.slice(2), none: m.copyNodes(g, []) }))""")
    assert out["clip"]["edges"] == [{"from": "n1", "to": "n2"}] and "picked" not in out["clip"]["nodes"][0]
    assert out["ids"] == ["n4", "n5"]
    assert out["added"][0] == {"id": "n4", "type": "picture", "x": 10, "y": 20, "settings": {"prompt": "a fox"}}
    assert out["edges"] == [{"from": "n4", "to": "n5"}] and out["none"] is None


# ---------------------------------------------------------------- canvas-layout.js
def test_tidy_lays_each_line_left_to_right_and_stacks_branches():
    out = _run("canvas-layout", """const g = { nodes: [
        { id: 'p', x: 500, y: 300 }, { id: 'm1', x: 0, y: 900 }, { id: 'm2', x: 10, y: 10 }, { id: 'f', x: 7, y: 7 }, { id: 'lone', x: 0, y: 2000 }],
        edges: [{ from: 'p', to: 'm1' }, { from: 'p', to: 'm2' }, { from: 'm1', to: 'f' }] };
      const t = m.tidy(g, {}, { gapX: 60, gapY: 40, left: 0, top: 0 });
      console.log(JSON.stringify(Object.fromEntries(t.nodes.map((n) => [n.id, [n.x, n.y]]))))""")
    assert out["p"][0] == 0 and out["m1"][0] == out["m2"][0] == 270 and out["f"][0] == 540
    assert out["m2"][1] < out["m1"][1]                     # m2 sat higher, so it stays on top
    assert out["f"][1] == out["m1"][1]                     # a straight line stays on one row
    assert out["lone"] == [0, max(out["m1"][1], out["m2"][1]) + 190]


# ---------------------------------------------------------------- canvas-keys.js
@pytest.mark.parametrize("event, typing, action", [
    ({"key": "z", "code": "KeyZ", "metaKey": True}, False, "undo"),
    ({"key": "z", "code": "KeyZ", "metaKey": True, "shiftKey": True}, False, "redo"),
    ({"key": "Ï", "code": "KeyF", "altKey": True, "shiftKey": True}, False, "tidy"),
    ({"key": "g", "code": "KeyG", "metaKey": True, "shiftKey": True}, False, "ungroup"),
    ({"key": "g", "code": "KeyG", "metaKey": True}, False, "group"),
    ({"key": "Backspace", "code": "Backspace"}, False, "delete"),
    ({"key": "Backspace", "code": "Backspace"}, True, None),
    ({"key": "Escape", "code": "Escape"}, True, "escape"),
    ({"key": "v", "code": "KeyV"}, False, "pointer"),
    ({"key": "v", "code": "KeyV", "metaKey": True}, False, "paste"),
    ({"key": "0", "code": "Digit0", "metaKey": True}, False, "fit"),
    ({"key": "Tab", "code": "Tab"}, False, "add"),
    ({"key": "?", "code": "Slash", "shiftKey": True}, False, "help"),
    ({"key": "a", "code": "KeyA", "ctrlKey": True}, False, None),   # on a Mac, Ctrl is not the shortcut key
    ({"key": "v", "code": "", "metaKey": True}, False, "paste"),    # no code: the letter decides
    ({"key": "", "code": "Slash", "shiftKey": True}, False, "help"),
    ({"key": "ƒ", "code": "", "altKey": True, "shiftKey": True}, False, None),   # neither place nor letter
])
def test_each_key_means_one_thing_and_typing_in_a_box_is_left_alone(event, typing, action):
    out = _run("canvas-keys", f"console.log(JSON.stringify(m.actionFor({json.dumps(event)}, {{ typing: {json.dumps(typing)}, mac: true }})))")
    assert out == action


def test_every_shortcut_says_what_it_does():
    rows = _run("canvas-keys", "console.log(JSON.stringify(m.SHORTCUTS.map(([a, w, k]) => [a, w, k])))")
    assert all(a and w and k for a, w, k in rows) and len({a for a, _, _ in rows}) == len(rows)


@pytest.mark.parametrize("event, intent", [
    ({"deltaY": 4.5, "ctrlKey": True}, "zoom"),          # a trackpad pinch
    ({"deltaY": 100}, "zoom"),                           # a mouse wheel notch
    ({"deltaY": 3}, "pan"),                              # a slow two-finger scroll
    ({"deltaX": 12, "deltaY": 100}, "pan"),
    ({"deltaY": 3, "deltaMode": 1}, "zoom"),             # Firefox counts mouse wheels in lines
])
def test_a_wheel_event_zooms_or_pans(event, intent):
    assert _run("canvas-view", f"console.log(JSON.stringify(m.wheelIntent({json.dumps(event)})))") == intent


def test_where_you_left_a_board_is_kept_in_this_browser_and_a_missing_store_is_fine():
    out = _run("canvas-view", """const store = new Map();
      const storage = { setItem: (k, v) => store.set(k, v), getItem: (k) => store.get(k) ?? null };
      m.rememberView(storage, 'graph-1', { x: 10, y: -4, k: 9 });
      m.rememberView(storage, null, { x: 1, y: 1, k: 1 });          // a board not saved yet keeps nothing
      const broken = { setItem: () => { throw new Error('blocked'); }, getItem: () => { throw new Error('blocked'); } };
      m.rememberView(broken, 'graph-1', { x: 0, y: 0, k: 1 });
      store.set('assetfurnace.canvas.view.graph-2', '{"x": "a"}');
      console.log(JSON.stringify([m.recallView(storage, 'graph-1'), m.recallView(storage, 'graph-2'), m.recallView(broken, 'graph-1'),
        m.recallView(null, 'graph-1'), store.size]))""")
    assert out == [{"x": 10, "y": -4, "k": 2.5}, None, None, None, 2]


def test_the_quick_view_camera_stands_back_far_enough_to_see_the_whole_model():
    out = _run("canvas-view", "console.log(JSON.stringify([m.fitDistance(2, 90, 1), m.fitDistance(4, 35), m.fitDistance(0, 35)]))")
    assert out[0] == pytest.approx(1.0)                    # a 90° view sees 2 across from 1 away
    assert out[1] > 2 * 1.35 * 2 and out[2] > 0             # narrower view, further back; a flat model still has a camera
