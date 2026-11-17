"""The canvas: step nodes joined by lines, run as ordinary chain runs. See docs/canvas.md.

A graph is saved in `output/.graphs.json`, beside the run history. `plan_graph` turns one
into stages: each stage is one `activity_api.Chain` (the same run Create sends), and a
node with several lines out, or a Split, starts new stages that carry on from what the
stage before made. `GraphRunner` runs the stages one after another through the studio's
own `ChainRunner`, so nothing here starts a model by itself.
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

GRAPHS_NAME = ".graphs.json"
GRAPH_ID = re.compile(r"^graph-[0-9a-f]{8}$")
NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
SCHEMA = 1
MAX_NODES = 60
MAX_EDGES = 120
NAME_LIMIT = 80
TEXT_LIMIT = 2000
KEEP_RUNS = 10     # runs remembered for a canvas to read back
RECIPES = Path(__file__).resolve().parents[1] / "image_to_3dlab" / "prompt_recipes.json"
_LOCK = threading.Lock()   # the server answers on threads; one write at a time

# What each node is called on screen, what it takes in, what it makes, and its runner step.
LABELS = {"picture": "Picture", "model": "3D model", "split": "Split into props", "finish": "Finish",
          "rig": "Rig", "move": "Move", "export": "Export"}
TAKES = {"picture": (), "model": ("picture",), "split": ("model",), "finish": ("model",),
         "rig": ("model", "finished", "props"), "move": ("rigged",),
         "export": ("model", "finished", "rigged", "animated", "props")}
MAKES = {"picture": "picture", "model": "model", "split": "props", "finish": "finished",
         "rig": "rigged", "move": "animated", "export": None}
STEP = {"picture": "picture", "model": "model", "split": "split", "finish": "finished",
        "rig": "rigged", "move": "animated"}
# the runner's `made` key each node's file lands under (split: the folder its props went to)
MADE_KEY = {"picture": "picture", "model": "model", "split": "split", "finish": "finished",
            "rig": "rigged", "move": "animated"}
GOALS = {"character": "a character", "prop": "one prop", "set": "a sheet of nine", "picture": "just a picture"}
TAKE_WORDS = {"picture": "a picture", "model": "a 3D model", "finished": "a game-ready model",
              "rigged": "a rigged model", "animated": "a moving model", "props": "props"}


# ---------------------------------------------------------------- the saved graphs
def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_settings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    kept = {}
    for key in ("prompt", "goal", "file", "engine", "picture_engine", "move"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            kept[key] = value.strip()[:TEXT_LIMIT]
    return kept


def clean_graph(raw: Any) -> dict[str, Any]:
    """The graph as it is stored: known fields only, sizes capped. A half-drawn graph is fine;
    `plan_graph` is the full check, made when it runs."""
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes", []), list) or not isinstance(raw.get("edges", []), list):
        raise ValueError("a graph is {name, nodes: [...], edges: [...]}")
    nodes, edges = raw.get("nodes", []), raw.get("edges", [])
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise ValueError(f"a graph holds up to {MAX_NODES} nodes and {MAX_EDGES} lines")
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
        clean_nodes.append({"id": n["id"], "type": n["type"], "x": x, "y": y, "settings": _clean_settings(n.get("settings"))})
    clean_edges = []
    for e in edges:
        if not (isinstance(e, dict) and NODE_ID.fullmatch(str(e.get("from", ""))) and NODE_ID.fullmatch(str(e.get("to", "")))):
            raise ValueError(f"not a line: {str(e)[:80]}")
        clean_edges.append({"from": e["from"], "to": e["to"]})
    graph = {"schema": SCHEMA, "id": raw.get("id") if GRAPH_ID.fullmatch(str(raw.get("id") or "")) else None,
             "name": _clean_text(raw.get("name"), NAME_LIMIT) or "Untitled canvas",
             "nodes": clean_nodes, "edges": clean_edges}
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
    kept unless the graph brings its own."""
    graph = clean_graph(raw)
    with _LOCK:
        graphs = _read(output)
        old = next((g for g in graphs if g["id"] == graph["id"]), None) if graph["id"] else None
        if old is None:
            taken = {g["id"] for g in graphs}
            graph["id"] = graph["id"] if graph["id"] and graph["id"] not in taken else f"graph-{os.urandom(4).hex()}"
            while graph["id"] in taken:
                graph["id"] = f"graph-{os.urandom(4).hex()}"
        elif "last_run" not in graph and "last_run" in old:
            graph["last_run"] = old["last_run"]
        graph["updated"] = int(now())
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
    if not graph_id:
        return
    with _LOCK:
        graphs = _read(output)
        for g in graphs:
            if g["id"] == graph_id:
                g["last_run"] = last_run
                _write(output, graphs)
                return


# ---------------------------------------------------------------- graph -> stages (pure)
@dataclass
class Stage:
    """One ordinary run: the steps of a straight line of nodes."""

    id: str
    nodes: list[str]
    steps: list[str]
    after: str | None = None     # the stage whose results this one starts from
    each_prop: bool = False      # run once for each prop the stage before split off
    want: str = "character"
    prompt: str | None = None
    picture_engine: str | None = None
    engine: str | None = None
    first_move: str | None = None
    finish_settings: dict[str, Any] = field(default_factory=dict)
    made: dict[str, str] = field(default_factory=dict)   # a picture already made, for a root stage

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "nodes": self.nodes, "steps": self.steps, "after": self.after,
                "each_prop": self.each_prop}


@dataclass
class Plan:
    stages: list[Stage]
    exports: dict[str, str]      # export node -> the node whose files it lists
    labels: dict[str, str]       # node id -> what it is called
    types: dict[str, str]        # node id -> its type


def _load_recipes() -> dict[str, Any] | None:
    try:
        return json.loads(RECIPES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _inside_output(relative: str) -> bool:
    path = PurePosixPath(str(relative))
    return bool(relative) and not path.is_absolute() and ".." not in path.parts and "\\" not in relative


def plan_graph(raw: Any, recipes: dict[str, Any] | None = None) -> Plan:
    """The stages a graph runs as, parents before children. Raises ValueError, in words a
    person can act on, for a graph that cannot run."""
    from image_to_3dlab.agent_plan import compose_prompt

    graph = clean_graph(raw)
    nodes = {}
    for n in graph["nodes"]:
        if n["id"] in nodes:
            raise ValueError(f"The node id {n['id']} is used twice.")
        nodes[n["id"]] = n
    if not any(n["type"] == "picture" for n in nodes.values()):
        raise ValueError("Add a Picture node to start.")
    label = {i: LABELS[n["type"]] for i, n in nodes.items()}
    parent: dict[str, str] = {}
    children: dict[str, list[str]] = {i: [] for i in nodes}
    for e in graph["edges"]:
        a, b = e["from"], e["to"]
        if a not in nodes or b not in nodes:
            raise ValueError("A line goes to a missing node. Delete it and draw it again.")
        if b in parent:
            raise ValueError(f"{label[b]} has two lines coming in. Each node takes one line in.")
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
        if kind != "picture" and i not in parent:
            raise ValueError(f"Every line of steps starts with a Picture: {label[i]} has nothing coming in.")
        if kind == "picture":
            if s.get("goal", "character") not in GOALS:
                raise ValueError(f"A Picture is for {', '.join(GOALS.values())}.")
            if s.get("file") and not _inside_output(s["file"]):
                raise ValueError("Picture: that is not a file in output/.")
            if not s.get("file") and not s.get("prompt"):
                raise ValueError("Picture: describe it, or pick a picture already made.")
        if kind == "model" and not s.get("engine"):
            raise ValueError("3D model: pick an engine.")
        if kind == "move" and not s.get("move"):
            raise ValueError("Move: pick a move.")
    # a node with one line in each, from a Picture that has none, cannot loop; checked anyway
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
        if n["type"] == "split" and not nodes[root_of[i]]["settings"].get("file") \
                and nodes[root_of[i]]["settings"].get("goal", "character") != "set":
            raise ValueError("Split into props needs a sheet of nine: set its Picture to a sheet of nine.")

    stages: list[Stage] = []
    exports: dict[str, str] = {}

    def step_children(i: str) -> list[str]:
        out = []
        for c in children[i]:
            if nodes[c]["type"] == "export":
                exports[c] = i
            else:
                out.append(c)
        return out

    def walk(start: str, after: Stage | None, each_prop: bool) -> None:
        root = nodes[root_of[start]]
        goal = root["settings"].get("goal", "character")
        stage = Stage(id=f"s{len(stages) + 1}", nodes=[], steps=[], after=after.id if after else None,
                      each_prop=each_prop, want="prop" if each_prop else goal)
        stages.append(stage)
        at = start
        while True:
            n = nodes[at]
            s = n["settings"]
            if n["type"] == "picture" and s.get("file"):
                stage.made["picture"] = s["file"]
            else:
                stage.steps.append(STEP[n["type"]])
            stage.nodes.append(at)
            if n["type"] == "picture" and not s.get("file"):
                stage.prompt = compose_prompt(s["prompt"], goal, recipes)
                stage.picture_engine = s.get("picture_engine")
            elif n["type"] == "model":
                stage.engine = s["engine"]
            elif n["type"] == "move":
                stage.first_move = s["move"]
            elif n["type"] == "finish":
                stage.finish_settings = {"height_m": 1.8} if goal == "character" and not each_prop else {"upright": True}
            nexts = step_children(at)
            if n["type"] == "split" or len(nexts) != 1:
                break
            at = nexts[0]
        if n["type"] == "split" and "split" in stage.steps:
            stage.want = "set"
        for c in step_children(at):
            walk(c, stage, n["type"] == "split")

    for i, n in nodes.items():
        if n["type"] == "picture":
            walk(i, None, False)
    return Plan(stages=stages, exports=exports, labels=label, types={i: n["type"] for i, n in nodes.items()})


def describe_plan(plan: Plan) -> list[str]:
    """The plan in plain lines, for the canvas to show before it runs."""
    index = {s.id: n for n, s in enumerate(plan.stages, start=1)}
    lines = []
    for n, stage in enumerate(plan.stages, start=1):
        names = " → ".join(plan.labels[i] for i in stage.nodes)
        if stage.each_prop:
            lines.append(f"Run {n}, once for each prop from run {index[stage.after]}: {names}")
        elif stage.after:
            lines.append(f"Run {n}, from what run {index[stage.after]} made: {names}")
        else:
            lines.append(f"Run {n}: {names}")
    return lines


def instances(stage: Stage, parent_made: list[dict[str, str]], props_of: Callable[[str], list[str] | None]) -> list[dict[str, Any]]:
    """The runs one stage makes: once for a first stage, once per run of the stage before,
    and for a stage after a Split, once per prop that split made. Each starts from `made`."""
    if stage.after is None:
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

    def to_dict(self) -> dict[str, Any]:
        results = {k: list(v) for k, v in self.results.items()}
        for export, source in self.plan.exports.items():
            results[export] = list(results.get(source, []))
        return {"id": self.id, "graph": self.graph_id, "name": self.name, "status": self.status,
                "stage": self.stage, "stages": [s.to_dict() for s in self.plan.stages],
                "plan": describe_plan(self.plan), "chains": list(self.chains),
                "current_chain": self.current_chain, "results": results, "error": self.error,
                "elapsed": round(time.time() - self.started, 1)}


class GraphRunner:
    """Runs a graph's stages one after another, each through the studio's ChainRunner."""

    def __init__(self, output: Path, chains: ChainRunner, *, recipes: dict[str, Any] | None = None,
                 poll: float = 2.0, sleep: Callable[[float], None] = time.sleep):
        self.output, self.chains = output, chains
        self.recipes = recipes if recipes is not None else _load_recipes()
        self.poll, self.sleep = poll, sleep
        self.runs: dict[str, GraphRun] = {}
        self.lock = threading.Lock()

    def running(self) -> GraphRun | None:
        return next((r for r in self.runs.values() if r.status == "running"), None)

    def plan(self, graph: dict[str, Any]) -> Plan:
        return plan_graph(graph, self.recipes)

    def start(self, graph: dict[str, Any], *, client: str | None = None, background: bool = True,
              before_stage: Callable[[GraphRun, int], Any] | None = None) -> GraphRun:
        plan = self.plan(graph)   # refused here, before anything starts
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
        failed = False
        try:
            for index, stage in enumerate(run.plan.stages):
                run.stage = index
                if before_stage:
                    before_stage(run, index)
                if run.cancel_requested:
                    break
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
            chain = Chain(title=title, steps=list(stage.steps), want=stage.want, prompt=stage.prompt,
                          engine=stage.engine, first_move=stage.first_move, picture_engine=stage.picture_engine,
                          finish_settings=dict(stage.finish_settings), made=dict(job["made"]), client=client,
                          model_seed=int.from_bytes(os.urandom(3), "big") if "model" in stage.steps else None)
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
        """What each node of the stage made in this run, or why it did not."""
        for node_id in stage.nodes:
            kind = run.plan.types[node_id]
            file = chain.made.get(MADE_KEY[kind])
            if kind == "split" and STEP[kind] in chain.done:
                for prop in split_props(self.output, file or ""):
                    self._add(run, node_id, {"status": "done", "file": prop, "label": PurePosixPath(prop).parent.name})
                continue
            if file and (STEP[kind] in chain.done or STEP[kind] not in stage.steps):
                entry = {"status": "done", "file": file}
            elif chain.status != "done" and chain.current == STEP[kind]:
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
