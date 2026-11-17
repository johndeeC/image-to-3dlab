"""The canvas: step boxes (nodes) joined by lines, run as ordinary chain runs. See docs/canvas.md.

A graph is saved in `output/.graphs.json`, beside the run history. `plan_graph` turns one
into stages: each stage is one `activity_api.Chain` (the same run Create sends), and a box
with several lines out, a Split, or a Picture asked for several options starts new stages
that carry on from what the stage before made. `plan_from` plans one box (or one box and
everything after it) from what its parents made last time. `GraphRunner` runs the stages one
after another through the studio's own `ChainRunner`, so nothing here starts a model itself.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from activity_api import Chain, ChainRunner

from image_to_3dlab.agent_plan import FINISH_DETAILS, compose_prompt

GRAPHS_NAME = ".graphs.json"
GRAPH_ID = re.compile(r"^graph-[0-9a-f]{8}$")
NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
GROUP_ID = re.compile(r"^g\d{1,4}$")
SCHEMA = 2                   # 2 added groups, picked results, the view and typed settings; 1 still reads
MAX_NODES = 60
MAX_EDGES = 120
NAME_LIMIT = 80
TEXT_LIMIT = 2000
KEEP_RUNS = 10               # runs remembered for a canvas to read back
RECIPES = Path(__file__).resolve().parents[1] / "image_to_3dlab" / "prompt_recipes.json"
_LOCK = threading.Lock()     # the server answers on threads; one write at a time

# What each box is called, what it takes in, what it makes, and its runner step.
# viewer/studio/canvas.js has the same table; tests/test_studio_canvas.py checks they agree.
LABELS = {"picture": "Picture", "asset": "Your 3D model", "back": "Front + back", "model": "3D model",
          "split": "Split into props", "finish": "Finish", "rig": "Rig", "move": "Move", "export": "Export"}
TAKES = {"picture": (), "asset": (), "back": ("picture",), "model": ("picture", "pair"), "split": ("model",),
         "finish": ("model",), "rig": ("model", "finished", "props"), "move": ("rigged",),
         "export": ("model", "finished", "rigged", "animated", "props")}
MAKES = {"picture": "picture", "asset": "model", "back": "pair", "model": "model", "split": "props",
         "finish": "finished", "rig": "rigged", "move": "animated", "export": None}
STEP = {"picture": "picture", "back": "back", "model": "model", "split": "split", "finish": "finished",
        "rig": "rigged", "move": "animated"}
# the runner's `made` key each box's file lands under (split: the folder its props went to)
MADE_KEY = {"picture": "picture", "asset": "model", "back": "back", "model": "model", "split": "split",
            "finish": "finished", "rig": "rigged", "move": "animated"}
ROOTS = ("picture", "asset")
GOALS = {"character": "a character", "prop": "one prop", "set": "a sheet of nine", "picture": "just a picture"}
TAKE_WORDS = {"picture": "a picture", "pair": "a front and back picture", "model": "a 3D model",
              "finished": "a game-ready model", "rigged": "a rigged model", "animated": "a moving model", "props": "props"}
SHAPES = {"square": (1024, 1024), "tall": (832, 1216), "wide": (1216, 832)}

# Every setting a box keeps, and what it may be. viewer/studio/canvas-settings.js draws the same
# list (tests/test_studio_canvas.py checks the keys agree). A value that does not fit is dropped.
SETTINGS: dict[str, dict[str, tuple]] = {
    "picture": {"prompt": ("text",), "goal": ("choice", *GOALS), "file": ("path",), "picture_engine": ("text",),
                "count": ("int", 1, 4), "shape": ("choice", *SHAPES)},
    "asset": {"file": ("path",), "goal": ("choice", "character", "prop")},
    "back": {},
    "model": {"engine": ("text",)},
    "split": {"names": ("text",)},
    "finish": {"detail": ("choice", *FINISH_DETAILS), "pixel_match": ("bool",),
               "texture_size": ("choice", 512, 1024, 2048, 4096), "surface_maps": ("bool",),
               "cleanup": ("choice", "auto", "fast", "rebuild"), "height_m": ("float", 0.01, 100.0), "upright": ("bool",)},
    "rig": {"bone_names": ("choice", "numbered", "unity")},
    "move": {"move": ("text",), "speed": ("float", 0.5, 2.0), "in_place": ("bool",)},
    "export": {},
}


class Stale(RuntimeError):
    """The canvas changed since it was read (another tab, or an agent): read it again first."""


# ---------------------------------------------------------------- the saved graphs
def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _inside_output(relative: str) -> bool:
    path = PurePosixPath(str(relative))
    return bool(relative) and not path.is_absolute() and ".." not in path.parts and "\\" not in str(relative)


def _setting(kind: tuple, value: Any) -> Any:
    """The value as the box keeps it, or None when it does not fit."""
    what = kind[0]
    if value is None or value == "":
        return None
    if what == "text":
        return str(value).strip()[:TEXT_LIMIT] or None
    if what == "path":
        return str(value).strip() if _inside_output(str(value).strip()) else None
    if what == "bool":
        return value if isinstance(value, bool) else {"true": True, "false": False, "1": True, "0": False}.get(str(value).lower())
    if what == "choice":
        for choice in kind[1:]:
            if str(choice) == str(value):
                return choice
        return None
    try:
        number = int(value) if what == "int" else float(value)
    except (TypeError, ValueError):
        return None
    return number if kind[1] <= number <= kind[2] else None


def clean_settings(node_type: str, raw: Any) -> dict[str, Any]:
    kept = {}
    for key, kind in SETTINGS.get(node_type, {}).items():
        value = _setting(kind, (raw or {}).get(key) if isinstance(raw, dict) else None)
        if value is not None:
            kept[key] = value
    return kept


def clean_graph(raw: Any) -> dict[str, Any]:
    """The graph as it is stored: known fields only, sizes capped. A half-drawn graph is fine;
    `plan_graph` is the full check, made when it runs."""
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes", []), list) or not isinstance(raw.get("edges", []), list):
        raise ValueError("a graph is {name, nodes: [...], edges: [...]}")
    nodes, edges = raw.get("nodes", []), raw.get("edges", [])
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise ValueError(f"a graph holds up to {MAX_NODES} boxes and {MAX_EDGES} lines")
    clean_nodes = []
    for n in nodes:
        if not (isinstance(n, dict) and NODE_ID.fullmatch(str(n.get("id", "")))):
            raise ValueError(f"not a node: {str(n)[:80]}")
        if n.get("type") not in LABELS:
            raise ValueError(f"unknown node type {str(n.get('type'))[:40]!r}")
        try:
            x, y = round(float(n.get("x", 0)), 1), round(float(n.get("y", 0)), 1)
        except (TypeError, ValueError):
            x, y = 0.0, 0.0
        node = {"id": n["id"], "type": n["type"], "x": x, "y": y, "settings": clean_settings(n["type"], n.get("settings"))}
        if isinstance(n.get("picked"), str) and _inside_output(n["picked"]):
            node["picked"] = n["picked"]
        clean_nodes.append(node)
    clean_edges = []
    for e in edges:
        if not (isinstance(e, dict) and NODE_ID.fullmatch(str(e.get("from", ""))) and NODE_ID.fullmatch(str(e.get("to", "")))):
            raise ValueError(f"not a line: {str(e)[:80]}")
        clean_edges.append({"from": e["from"], "to": e["to"]})
    ids = {n["id"] for n in clean_nodes}
    groups = []
    for g in raw.get("groups") or []:
        if isinstance(g, dict) and GROUP_ID.fullmatch(str(g.get("id", ""))) and isinstance(g.get("nodes"), list):
            inside = [i for i in g["nodes"] if i in ids]
            if inside:
                groups.append({"id": g["id"], "label": _clean_text(g.get("label"), 60) or "Group", "nodes": inside})
    graph = {"schema": SCHEMA, "id": raw.get("id") if GRAPH_ID.fullmatch(str(raw.get("id") or "")) else None,
             "name": _clean_text(raw.get("name"), NAME_LIMIT) or "Untitled canvas",
             "nodes": clean_nodes, "edges": clean_edges, "groups": groups}
    view = raw.get("view")
    if isinstance(view, dict):
        try:
            graph["view"] = {"x": round(float(view["x"]), 1), "y": round(float(view["y"]), 1),
                             "k": min(2.5, max(0.2, round(float(view["k"]), 3)))}
        except (KeyError, TypeError, ValueError):
            pass
    if isinstance(raw.get("last_run"), dict):
        graph["last_run"] = raw["last_run"]
    if "updated" in raw:
        graph["updated"] = raw["updated"]
    return graph


def _read(output: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((output / GRAPHS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    graphs = []
    for row in data.get("graphs", []) if isinstance(data, dict) and isinstance(data.get("graphs"), list) else []:
        try:
            graph = clean_graph(row)
        except ValueError:
            continue
        if graph["id"]:
            graphs.append(graph)
    return graphs


def _write(output: Path, graphs: list[dict[str, Any]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    partial = output / (GRAPHS_NAME + ".part")
    partial.write_text(json.dumps({"graphs": graphs}, indent=1), encoding="utf-8")
    partial.replace(output / GRAPHS_NAME)   # never half a file


def list_graphs(output: Path) -> list[dict[str, Any]]:
    """Every saved graph, the one changed last first."""
    return sorted(_read(output), key=lambda g: g.get("updated", 0), reverse=True)


def get_graph(output: Path, graph_id: str) -> dict[str, Any]:
    found = next((g for g in _read(output) if g["id"] == graph_id), None)
    if found is None:
        raise ValueError(f"no such canvas: {graph_id}")
    return found


def save_graph(output: Path, raw: Any, *, now: Callable[[], float] = time.time) -> dict[str, Any]:
    """Save a graph, new or changed. A new one gets its id here. The last run's results are
    kept unless the graph brings its own. A graph that says when it was read (`updated`) is
    refused with Stale when someone saved it since, so a tab and an agent can't overwrite
    each other's changes."""
    graph = clean_graph(raw)
    with _LOCK:
        graphs = _read(output)
        old = next((g for g in graphs if g["id"] == graph["id"]), None) if graph["id"] else None
        if old is None:
            taken = {g["id"] for g in graphs}
            graph["id"] = graph["id"] if graph["id"] and graph["id"] not in taken else f"graph-{os.urandom(4).hex()}"
            while graph["id"] in taken:
                graph["id"] = f"graph-{os.urandom(4).hex()}"
        else:
            if "updated" in graph and graph["updated"] != old.get("updated"):
                raise Stale("This canvas was changed somewhere else since it was opened. Open it again to see the changes.")
            if "last_run" not in graph and "last_run" in old:
                graph["last_run"] = old["last_run"]
        graph["updated"] = max(int(now()), int((old or {}).get("updated", 0)) + 1)
        graphs = [g for g in graphs if g["id"] != graph["id"]] + [graph]
        _write(output, graphs)
    return graph


def delete_graph(output: Path, graph_id: str) -> None:
    """Remove the board. What its runs made stays in the Library."""
    with _LOCK:
        graphs = _read(output)
        if not any(g["id"] == graph_id for g in graphs):
            raise ValueError(f"no such canvas: {graph_id}")
        _write(output, [g for g in graphs if g["id"] != graph_id])


def _keep_run(output: Path, graph_id: str | None, last_run: dict[str, Any]) -> None:
    """Remember a run on its graph. Only the boxes this run touched are replaced, so running
    one box keeps what the others made before."""
    if not graph_id:
        return
    with _LOCK:
        graphs = _read(output)
        for g in graphs:
            if g["id"] == graph_id:
                before = (g.get("last_run") or {}).get("results") or {}
                g["last_run"] = {**last_run, "results": {**before, **last_run.get("results", {})}}
                _write(output, graphs)
                return


# ---------------------------------------------------------------- graph -> stages (pure)
@dataclass
class Stage:
    """One ordinary run: the steps of a straight line of boxes."""

    id: str
    nodes: list[str]
    steps: list[str]
    after: str | None = None     # the stage whose results this one starts from
    each_prop: bool = False      # run once for each prop the stage before split off
    want: str = "character"
    prompt: str | None = None
    picture_engine: str | None = None
    picture_settings: dict[str, Any] = field(default_factory=dict)
    count: int = 1               # a Picture asked for several options: that many runs
    engine: str | None = None
    first_move: str | None = None
    finish_settings: dict[str, Any] = field(default_factory=dict)
    split_settings: dict[str, Any] = field(default_factory=dict)
    rig_settings: dict[str, Any] = field(default_factory=dict)
    move_settings: dict[str, Any] = field(default_factory=dict)
    made: dict[str, str] = field(default_factory=dict)        # a file already made, for a first stage
    seeds: list[dict[str, Any]] | None = None                 # runs to start from (one box run on its own)
    waits_for_pick: bool = False                              # follows a Picture with options to pick from

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "nodes": self.nodes, "steps": self.steps, "after": self.after,
                "each_prop": self.each_prop, "count": self.count, "waits_for_pick": self.waits_for_pick}


@dataclass
class Plan:
    stages: list[Stage]
    exports: dict[str, str]      # export box -> the box whose files it lists
    labels: dict[str, str]       # box id -> what it is called
    types: dict[str, str]        # box id -> its type


def _load_recipes() -> dict[str, Any] | None:
    try:
        return json.loads(RECIPES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@dataclass
class _Graph:
    """A checked graph: boxes by id, one parent each, children in order, and each box's first box."""

    nodes: dict[str, dict[str, Any]]
    parent: dict[str, str]
    children: dict[str, list[str]]
    root_of: dict[str, str]
    label: dict[str, str]


def _check(raw: Any, keep: set[str] | None = None) -> _Graph:
    """Check a graph can run, in words a person can act on. `keep` limits the check to those
    boxes (running one box ignores a half-drawn branch elsewhere)."""
    graph = clean_graph(raw)
    nodes: dict[str, dict[str, Any]] = {}
    for n in graph["nodes"]:
        if n["id"] in nodes:
            raise ValueError(f"The box id {n['id']} is used twice.")
        if keep is None or n["id"] in keep:
            nodes[n["id"]] = n
    if not any(n["type"] in ROOTS for n in nodes.values()):
        raise ValueError("Add a Picture box to start.")
    label = {i: LABELS[n["type"]] for i, n in nodes.items()}
    parent: dict[str, str] = {}
    children: dict[str, list[str]] = {i: [] for i in nodes}
    for e in graph["edges"]:
        a, b = e["from"], e["to"]
        if keep is not None and (a not in nodes or b not in nodes):
            continue
        if a not in nodes or b not in nodes:
            raise ValueError("A line goes to a missing box. Delete it and draw it again.")
        if b in parent:
            raise ValueError(f"{label[b]} has two lines coming in. Each box takes one line in.")
        to, made = nodes[b]["type"], MAKES[nodes[a]["type"]]
        if not TAKES[to]:
            raise ValueError(f"{label[b]} takes no line in: it starts a run.")
        if made not in TAKES[to]:
            takes = " or ".join(TAKE_WORDS[t] for t in TAKES[to])
            raise ValueError(f"{label[b]} takes {takes}, and {label[a]} makes {TAKE_WORDS.get(made, 'nothing')}.")
        parent[b] = a
        children[a].append(b)
    for i, n in nodes.items():
        s, kind = n["settings"], n["type"]
        if kind not in ROOTS and i not in parent:
            raise ValueError(f"Every line of steps starts with a Picture: {label[i]} has nothing coming in.")
        if kind == "picture" and not s.get("file") and not s.get("prompt"):
            raise ValueError("Picture: describe it, or pick a picture already made.")
        if kind == "asset" and not s.get("file"):
            raise ValueError("Your 3D model: pick one from the Library.")
        if kind == "model" and not s.get("engine"):
            raise ValueError("3D model: pick an engine.")
        if kind == "move" and not s.get("move"):
            raise ValueError("Move: pick a move.")
    root_of: dict[str, str] = {}
    for i in nodes:
        seen, at = set(), i
        while at in parent:
            if at in seen:
                raise ValueError("The lines go round in a loop.")
            seen.add(at)
            at = parent[at]
        root_of[i] = at
    for i, n in nodes.items():
        root = nodes[root_of[i]]
        if n["type"] == "split" and root["type"] == "picture" and not root["settings"].get("file") \
                and root["settings"].get("goal", "character") != "set":
            raise ValueError("Split into props needs a sheet of nine: set its Picture to a sheet of nine.")
    return _Graph(nodes, parent, children, root_of, label)


def _names(text: str) -> str:
    """Prop names as the props job takes them: one per line, a space inside a name made a dash."""
    parts = [p.strip() for line in str(text).splitlines() for p in line.split(",")]
    return "\n".join("-".join(p.split()) for p in parts if p)


def _finish(settings: dict[str, Any], goal: str) -> dict[str, Any]:
    """A Finish box's choices as the finish job takes them; the goal's defaults first, as Create's."""
    out: dict[str, Any] = {"height_m": 1.8} if goal == "character" else {"upright": True}
    out.update(FINISH_DETAILS.get(settings.get("detail", ""), {}))
    if settings.get("pixel_match") is False:
        out["skip_photo"] = True
    if "texture_size" in settings:
        out["texture_size"] = settings["texture_size"]
    if settings.get("surface_maps") is False:
        out["drop_surface_maps"] = True
    for key in ("cleanup", "height_m", "upright"):
        if key in settings:
            out[key] = settings[key]
    return out


class _Planner:
    def __init__(self, g: _Graph, recipes: dict[str, Any] | None):
        self.g, self.recipes = g, recipes
        self.stages: list[Stage] = []
        self.exports: dict[str, str] = {}

    def step_children(self, i: str) -> list[str]:
        out = []
        for c in self.g.children[i]:
            if self.g.nodes[c]["type"] == "export":
                self.exports[c] = i
            else:
                out.append(c)
        return out

    def in_props(self, i: str) -> bool:
        at = self.g.parent.get(i)
        while at is not None:
            if self.g.nodes[at]["type"] == "split":
                return True
            at = self.g.parent.get(at)
        return False

    def walk(self, start: str, after: Stage | None, each_prop: bool, *, seeds=None, single=False) -> Stage:
        g = self.g
        root = g.nodes[g.root_of[start]]
        goal = root["settings"].get("goal", "character")
        props = each_prop or self.in_props(start)
        stage = Stage(id=f"s{len(self.stages) + 1}", nodes=[], steps=[], after=after.id if after else None,
                      each_prop=each_prop, want="prop" if props else goal, seeds=seeds)
        if after is not None and after.count > 1:
            stage.waits_for_pick = True
        self.stages.append(stage)
        at = start
        while True:
            n = g.nodes[at]
            s, kind = n["settings"], n["type"]
            stage.nodes.append(at)
            if kind == "picture" and s.get("file"):
                stage.made["picture"] = s["file"]
            elif kind == "asset":
                stage.made["model"] = s["file"]
            else:
                stage.steps.append(STEP[kind])
            if kind == "picture" and not s.get("file"):
                stage.prompt = compose_prompt(s["prompt"], goal, self.recipes)
                stage.picture_engine = s.get("picture_engine")
                width, height = SHAPES[s.get("shape", "square")]
                stage.picture_settings = {"width": width, "height": height}
                stage.count = s.get("count", 1)
            elif kind == "model":
                stage.engine = s["engine"]
            elif kind == "split":
                stage.want = "set"
                if s.get("names"):
                    stage.split_settings = {"names": _names(s["names"])}
            elif kind == "finish":
                stage.finish_settings = _finish(s, "prop" if props else goal)
            elif kind == "rig" and s.get("bone_names"):
                stage.rig_settings = {"bone_names": s["bone_names"]}
            elif kind == "move":
                stage.first_move = s["move"]
                stage.move_settings = {k: s[k] for k in ("speed", "in_place") if k in s}
            nexts = self.step_children(at)
            if single or kind == "split" or stage.count > 1 or len(nexts) != 1:
                break
            at = nexts[0]
        if not single:
            for c in self.step_children(at):
                self.walk(c, stage, kind == "split")
        return stage

    def plan(self) -> Plan:
        return Plan(stages=self.stages, exports=self.exports, labels=self.g.label,
                    types={i: n["type"] for i, n in self.g.nodes.items()})


def plan_graph(raw: Any, recipes: dict[str, Any] | None = None) -> Plan:
    """The stages a whole graph runs as, parents before children. Raises ValueError, in words a
    person can act on, for a graph that cannot run."""
    planner = _Planner(_check(raw), recipes)
    for i, n in planner.g.nodes.items():
        if n["type"] in ROOTS:
            planner.walk(i, None, False)
    return planner.plan()


def _ancestors(raw: dict[str, Any], node_id: str) -> list[str]:
    parent = {e["to"]: e["from"] for e in raw.get("edges", [])}
    out, at = [], parent.get(node_id)
    while at is not None and at not in out:
        out.append(at)
        at = parent.get(at)
    return out[::-1]     # first box first


def _descendants(raw: dict[str, Any], node_id: str) -> list[str]:
    out, queue = [], [node_id]
    while queue:
        at = queue.pop(0)
        for e in raw.get("edges", []):
            if e["from"] == at and e["to"] not in out and e["to"] != node_id:
                out.append(e["to"])
                queue.append(e["to"])
    return out


def _chosen(g: _Graph, node_id: str, results: dict[str, list[dict[str, Any]]]) -> str:
    """The one file a box hands on: what it was given, the result picked, or its only result."""
    n = g.nodes[node_id]
    if n["type"] in ROOTS and n["settings"].get("file"):
        return n["settings"]["file"]
    done = [r["file"] for r in results.get(node_id, []) if r.get("status") == "done" and r.get("file")]
    if n.get("picked") in done:
        return n["picked"]
    if len(done) == 1:
        return done[0]
    if not done:
        raise ValueError(f"Run {g.label[node_id]} first: it has made nothing yet.")
    raise ValueError(f"Pick one of {g.label[node_id]}'s {len(done)} results first.")


def _seeds(g: _Graph, path: list[str], results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """What runs starting just below `path` (the boxes above, first box first) begin from: one run,
    or after a Split, one per prop that came through every box since."""
    split_at = max((i for i, b in enumerate(path) if g.nodes[b]["type"] == "split"), default=None)
    if split_at is None:
        made = {}
        for b in path:
            made[MADE_KEY[g.nodes[b]["type"]]] = _chosen(g, b, results)
        return [{"made": made, "label": None}]
    seeds = []
    for prop in [r for r in results.get(path[split_at], []) if r.get("status") == "done" and r.get("file")]:
        made, label = {"finished": prop["file"]}, prop.get("label")
        for b in path[split_at + 1:]:
            hit = next((r for r in results.get(b, []) if r.get("label") == label and r.get("status") == "done"), None)
            if hit is None:
                break
            made[MADE_KEY[g.nodes[b]["type"]]] = hit["file"]
        else:
            seeds.append({"made": made, "label": label})
    if not seeds:
        raise ValueError(f"Run {g.label[path[-1]]} first: it has made nothing yet.")
    return seeds


def plan_from(raw: Any, node_id: str, mode: str, results: dict[str, list[dict[str, Any]]] | None = None,
              recipes: dict[str, Any] | None = None) -> Plan:
    """Run one box from what the boxes above it made last time: `only` that box, `from` that box
    and everything below it, `after` everything below it (once a picture is picked)."""
    if mode not in ("only", "from", "after"):
        raise ValueError("mode is only, from or after")
    graph = clean_graph(raw)
    if not any(n["id"] == node_id for n in graph["nodes"]):
        raise ValueError(f"no box {node_id} on this canvas")
    up = _ancestors(graph, node_id)
    keep = {*up, node_id} | (set(_descendants(graph, node_id)) if mode != "only" else set())
    g = _check(graph, keep)
    results = results or {}
    planner = _Planner(g, recipes)
    if mode == "after":
        starts = planner.step_children(node_id)
        seeds = _seeds(g, [*up, node_id], results)
        if not starts:
            raise ValueError(f"Nothing comes after {g.label[node_id]}.")
        for c in starts:
            planner.walk(c, None, False, seeds=seeds)
        return planner.plan()
    if g.nodes[node_id]["type"] in ROOTS:
        planner.walk(node_id, None, False, single=mode == "only")
    else:
        planner.walk(node_id, None, False, seeds=_seeds(g, up, results), single=mode == "only")
    if mode == "only":
        planner.step_children(node_id)   # an Export below this box still lists what it makes
    return planner.plan()


def describe_plan(plan: Plan) -> list[str]:
    """The plan in plain lines, for the canvas to show before it runs."""
    index = {s.id: n for n, s in enumerate(plan.stages, start=1)}
    lines = []
    for n, stage in enumerate(plan.stages, start=1):
        names = " → ".join(plan.labels[i] for i in stage.nodes)
        if stage.count > 1:
            names += f" ({stage.count} options to pick from)"
        if stage.seeds and len(stage.seeds) > 1:
            lines.append(f"Run {n}, once for each of {len(stage.seeds)} props: {names}")
        elif stage.waits_for_pick:
            lines.append(f"Run {n}, once you pick a picture from run {index[stage.after]}: {names}")
        elif stage.each_prop:
            lines.append(f"Run {n}, once for each prop from run {index[stage.after]}: {names}")
        elif stage.after:
            lines.append(f"Run {n}, from what run {index[stage.after]} made: {names}")
        else:
            lines.append(f"Run {n}: {names}")
    return lines


def instances(stage: Stage, parent_made: list[dict[str, str]], props_of: Callable[[str], list[str] | None]) -> list[dict[str, Any]]:
    """The runs one stage makes: once for a first stage (or once per option a Picture is asked
    for, or once per seed it was given), once per run of the stage before, and for a stage after
    a Split, once per prop that split made. Each starts from `made`."""
    if stage.seeds is not None:
        return [{"made": dict(s["made"]), "label": s.get("label")} for s in stage.seeds]
    if stage.after is None:
        if stage.count > 1:
            return [{"made": dict(stage.made), "label": f"Option {i}"} for i in range(1, stage.count + 1)]
        return [{"made": dict(stage.made), "label": None}]
    runs = []
    for made in parent_made:
        if stage.each_prop:
            for prop in props_of(made.get("split", "")) or []:
                runs.append({"made": {"finished": prop}, "label": PurePosixPath(prop).parent.name})
        else:
            runs.append({"made": dict(made), "label": None})
    return runs


def split_props(output: Path, split_dir: str) -> list[str]:
    """The finished props of one split, as paths in output/: each prop's LOD0, by name."""
    if not _inside_output(split_dir):
        return []
    finished = output / split_dir / "finished"
    try:
        found = sorted(p for p in finished.iterdir() if (p / f"{p.name}_LOD0.glb").is_file())
    except OSError:
        return []
    return [f"{split_dir}/finished/{p.name}/{p.name}_LOD0.glb" for p in found]


# ---------------------------------------------------------------- running a graph
@dataclass
class GraphRun:
    graph_id: str | None
    name: str
    plan: Plan
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "running"
    stage: int = 0
    chains: list[str] = field(default_factory=list)
    current_chain: str | None = None
    results: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    error: str = ""
    cancel_requested: bool = False
    started: float = field(default_factory=time.time)

    def waiting_for_pick(self) -> list[str]:
        """The Picture boxes whose options wait for you to pick one."""
        by_id = {s.id: s for s in self.plan.stages}
        return list(dict.fromkeys(by_id[s.after].nodes[-1] for s in self.plan.stages if s.waits_for_pick))

    def to_dict(self) -> dict[str, Any]:
        results = {k: list(v) for k, v in self.results.items()}
        for export, source in self.plan.exports.items():
            results[export] = list(results.get(source, []))
        return {"id": self.id, "graph": self.graph_id, "name": self.name, "status": self.status,
                "stage": self.stage, "stages": [s.to_dict() for s in self.plan.stages],
                "plan": describe_plan(self.plan), "chains": list(self.chains),
                "current_chain": self.current_chain, "results": results, "error": self.error,
                "waiting_for_pick": self.waiting_for_pick(), "elapsed": round(time.time() - self.started, 1)}


class GraphRunner:
    """Runs a graph's stages one after another, each through the studio's ChainRunner."""

    def __init__(self, output: Path, chains: ChainRunner, *, recipes: dict[str, Any] | None = None,
                 poll: float = 2.0, sleep: Callable[[float], None] = time.sleep, random: Callable[[], int] | None = None):
        self.output, self.chains = output, chains
        self.recipes = recipes if recipes is not None else _load_recipes()
        self.poll, self.sleep = poll, sleep
        self.random = random or (lambda: int.from_bytes(os.urandom(3), "big"))
        self.runs: dict[str, GraphRun] = {}
        self.lock = threading.Lock()

    def running(self) -> GraphRun | None:
        return next((r for r in self.runs.values() if r.status == "running"), None)

    def plan(self, graph: dict[str, Any], node: str | None = None, mode: str = "all") -> Plan:
        if node is None or mode == "all":
            return plan_graph(graph, self.recipes)
        results = (graph.get("last_run") or {}).get("results") or {}
        return plan_from(graph, node, mode, results, self.recipes)

    def start(self, graph: dict[str, Any], *, node: str | None = None, mode: str = "all", client: str | None = None,
              background: bool = True, before_stage: Callable[[GraphRun, int], Any] | None = None) -> GraphRun:
        plan = self.plan(graph, node, mode)   # refused here, before anything starts
        with self.lock:
            if self.running() is not None:
                raise RuntimeError("a canvas is already running; wait for it to finish")
            run = GraphRun(graph_id=graph.get("id"), name=_clean_text(graph.get("name"), NAME_LIMIT) or "Canvas", plan=plan)
            recent = sorted(self.runs.values(), key=lambda r: r.started)[-(KEEP_RUNS - 1):]
            self.runs = {r.id: r for r in recent} | {run.id: run}   # finished runs pile up otherwise
        work = lambda: self._run(run, client, before_stage)
        if background:
            threading.Thread(target=work, daemon=True, name=f"graph-{run.id[:8]}").start()
        else:
            work()
        return run

    def cancel(self, run_id: str) -> bool:
        run = self.runs.get(run_id)
        if run is None or run.status != "running":
            return False
        run.cancel_requested = True
        if run.current_chain:
            self.chains.cancel(run.current_chain)
        return True

    def _run(self, run: GraphRun, client: str | None, before_stage) -> None:
        made_by: dict[str, list[dict[str, str]]] = {}   # stage id -> what each of its runs made
        waiting: set[str] = set()                        # stages that wait for a picture to be picked
        failed = False
        try:
            for index, stage in enumerate(run.plan.stages):
                run.stage = index
                if before_stage:
                    before_stage(run, index)
                if run.cancel_requested:
                    break
                if stage.waits_for_pick or stage.after in waiting:
                    waiting.add(stage.id)
                    self._mark(run, stage, "waiting")   # runs once you pick (Run after on the Picture)
                    continue
                if stage.after is not None and not made_by.get(stage.after):
                    self._mark(run, stage, "skipped")   # the stage it starts from made nothing
                    continue
                made_by[stage.id] = []
                jobs = instances(stage, made_by.get(stage.after, []), lambda d: split_props(self.output, d))
                if not jobs:
                    self._mark(run, stage, "skipped")   # a split that made no props
                for job in jobs:
                    if run.cancel_requested:
                        break
                    chain = self._chain(run, stage, job, client)
                    if chain is None:
                        break
                    self._results(run, stage, chain, job["label"])
                    if chain.status == "done":
                        made_by[stage.id].append({k: v for k, v in chain.made.items() if v})
                    else:
                        failed = True
                        run.error = run.error or chain.error
            run.status = "cancelled" if run.cancel_requested else "error" if failed else "done"
        except Exception as exc:   # a graph run must end, never hang as "running"
            run.status, run.error = "error", str(exc)
        finally:
            run.current_chain = None
            _keep_run(self.output, run.graph_id, {"status": run.status, "results": run.to_dict()["results"],
                                                   "error": run.error, "finished": int(time.time())})

    def _chain(self, run: GraphRun, stage: Stage, job: dict[str, Any], client: str | None) -> Chain | None:
        title = f"{run.name} · {job['label']}" if job["label"] else run.name
        while True:
            if run.cancel_requested:
                return None
            picture = {**stage.picture_settings, "seed": self.random()} if "picture" in stage.steps else {}
            chain = Chain(title=title, steps=list(stage.steps), want=stage.want, prompt=stage.prompt,
                          engine=stage.engine, first_move=stage.first_move, picture_engine=stage.picture_engine,
                          picture_settings=picture, finish_settings=dict(stage.finish_settings),
                          split_settings=dict(stage.split_settings), rig_settings=dict(stage.rig_settings),
                          move_settings=dict(stage.move_settings), made=dict(job["made"]), client=client,
                          model_seed=self.random() if "model" in stage.steps else None)
            run.current_chain = chain.id
            try:
                self.chains.submit(chain, background=False)
            except RuntimeError:     # someone else's run is going: wait for it, as a queue would
                run.current_chain = None
                self.sleep(self.poll)
                continue
            except ValueError as exc:
                chain.status, chain.error = "error", str(exc)
            run.chains.append(chain.id)
            run.current_chain = None
            return chain

    def _results(self, run: GraphRun, stage: Stage, chain: Chain, label: str | None) -> None:
        """What each box of the stage made in this run, or why it did not."""
        for node_id in stage.nodes:
            kind = run.plan.types[node_id]
            file = chain.made.get(MADE_KEY[kind])
            step = STEP.get(kind)
            if kind == "split" and step in chain.done:
                for prop in split_props(self.output, file or ""):
                    self._add(run, node_id, {"status": "done", "file": prop, "label": PurePosixPath(prop).parent.name,
                                             "dir": file})
                continue
            if file and (step in chain.done or step not in stage.steps):
                entry = {"status": "done", "file": file}
            elif chain.status != "done" and chain.current == step:
                entry = {"status": chain.status, "error": chain.error}
            else:
                entry = {"status": "skipped"}
            if label:
                entry["label"] = label
            self._add(run, node_id, entry)

    def _mark(self, run: GraphRun, stage: Stage, status: str) -> None:
        for node_id in stage.nodes:
            self._add(run, node_id, {"status": status})

    @staticmethod
    def _add(run: GraphRun, node_id: str, entry: dict[str, Any]) -> None:
        run.results.setdefault(node_id, []).append(entry)


UPLOAD_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
UPLOAD_LIMIT = 50 * 1024 * 1024


def keep_upload(output: Path, name: str, data: bytes) -> str:
    """A picture dropped on a Picture box, kept under output/images/uploads/ (where Create keeps
    its uploads) so the run can name it by path. Returns that path."""
    import io

    from PIL import Image

    suffix = Path(name or "").suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise ValueError("Pick a PNG, JPG or WebP picture.")
    if not data or len(data) > UPLOAD_LIMIT:
        raise ValueError("That picture is empty or over 50 MB.")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.verify()
    except Exception as exc:
        raise ValueError(f"That file is not a picture we can read: {exc}") from exc
    stem = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")[:40] or "upload"
    relative = f"images/uploads/{stem}__{os.urandom(4).hex()}{suffix}"
    path = output / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return relative
