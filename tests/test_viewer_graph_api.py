"""The canvas: a graph of step nodes, turned into ordinary chain runs (viewer/graph_api.py, docs/canvas.md)."""

from __future__ import annotations

import json
from pathlib import Path

import activity_api as aa
import graph_api as ga
import pytest

RECIPES = {"goals": {"character": {"wording": "A-pose"}, "prop": {"wording": "single object"},
                     "set": {"wording": "a 3x3 grid"}, "picture": {"wording": ""}}}


def node(id_, type_, **settings):
    return {"id": id_, "type": type_, "x": 0, "y": 0, "settings": settings}


def graph(nodes, edges, name="Knight"):
    return {"schema": 1, "id": "graph-00000001", "name": name, "nodes": nodes,
            "edges": [{"from": a, "to": b} for a, b in edges]}


CHARACTER = graph(
    [node("n1", "picture", prompt="a rune knight", goal="character"), node("n2", "model", engine="pixal3d"),
     node("n3", "finish"), node("n4", "rig"), node("n5", "move", move="walk")],
    [("n1", "n2"), ("n2", "n3"), ("n3", "n4"), ("n4", "n5")])

SHEET = graph(
    [node("s1", "picture", prompt="nine swords", goal="set"), node("s2", "model", engine="trellis"),
     node("s3", "split"), node("s4", "rig"), node("s5", "export")],
    [("s1", "s2"), ("s2", "s3"), ("s3", "s4"), ("s3", "s5")], name="Swords")


# ---------------------------------------------------------------- graph -> stages (pure)
def test_a_straight_line_of_nodes_is_one_run_like_creates():
    plan = ga.plan_graph(CHARACTER, RECIPES)
    assert len(plan.stages) == 1
    stage = plan.stages[0]
    assert stage.steps == ["picture", "model", "finished", "rigged", "animated"]
    assert stage.nodes == ["n1", "n2", "n3", "n4", "n5"]
    assert stage.after is None and not stage.each_prop
    assert stage.want == "character"
    assert stage.prompt == "a rune knight, A-pose"          # Create's own wording, added the same way
    assert stage.engine == "pixal3d" and stage.first_move == "walk"
    assert stage.finish_settings == {"height_m": 1.8}       # a character stands 1.8 m tall, as in Create


def test_a_split_ends_its_run_and_the_nodes_after_it_run_once_per_prop():
    plan = ga.plan_graph(SHEET, RECIPES)
    first, per_prop = plan.stages
    assert first.steps == ["picture", "model", "split"] and first.want == "set"
    assert first.prompt == "nine swords, a 3x3 grid"
    assert per_prop.steps == ["rigged"] and per_prop.nodes == ["s4"]
    assert per_prop.after == first.id and per_prop.each_prop
    assert per_prop.want == "prop"
    assert plan.exports == {"s5": "s3"}                       # Export lists the split's props; it runs nothing


def test_two_lines_out_of_a_node_share_what_came_before_it():
    g = graph([node("a", "picture", prompt="a fox", goal="prop"), node("b", "model", engine="sf3d"),
               node("c", "finish"), node("d", "rig")], [("a", "b"), ("b", "c"), ("b", "d")])
    shared, finish, rig = ga.plan_graph(g, RECIPES).stages
    assert shared.steps == ["picture", "model"]
    assert (finish.steps, finish.after, finish.each_prop) == (["finished"], shared.id, False)
    assert (rig.steps, rig.after) == (["rigged"], shared.id)
    assert finish.finish_settings == {"upright": True}       # a prop stands upright, as in Create


def test_one_picture_into_two_engines_draws_the_picture_once():
    g = graph([node("p", "picture", prompt="a lamp", goal="prop"), node("m1", "model", engine="pixal3d"),
               node("m2", "model", engine="trellis")], [("p", "m1"), ("p", "m2")])
    shared, one, two = ga.plan_graph(g, RECIPES).stages
    assert shared.steps == ["picture"]
    assert (one.steps, one.engine, two.steps, two.engine) == (["model"], "pixal3d", ["model"], "trellis")


def test_an_export_hanging_mid_line_does_not_break_the_run_in_two():
    g = graph([node("p", "picture", prompt="a lamp", goal="prop"), node("m", "model", engine="sf3d"),
               node("f", "finish"), node("x", "export")], [("p", "m"), ("m", "f"), ("m", "x")])
    plan = ga.plan_graph(g, RECIPES)
    assert [s.steps for s in plan.stages] == [["picture", "model", "finished"]]
    assert plan.exports == {"x": "m"}


def test_a_picture_already_made_is_used_instead_of_drawn():
    g = graph([node("p", "picture", file="images/fox.png", goal="prop"), node("m", "model", engine="sf3d")],
              [("p", "m")])
    stage = ga.plan_graph(g, RECIPES).stages[0]
    assert stage.steps == ["model"] and stage.made == {"picture": "images/fox.png"} and stage.prompt is None


def test_the_plan_reads_as_plain_lines():
    assert ga.describe_plan(ga.plan_graph(SHEET, RECIPES)) == [
        "Run 1: Picture → 3D model → Split into props",
        "Run 2, once for each prop from run 1: Rig",
    ]


@pytest.mark.parametrize("nodes, edges, words", [
    ([], [], "Add a Picture"),
    ([node("p", "picture", prompt="x"), node("m", "model", engine="sf3d")], [], "starts with a Picture"),
    ([node("p", "picture", goal="prop")], [], "Picture: describe it"),
    ([node("p", "picture", prompt="x"), node("q", "teleport")], [], "unknown node"),
    ([node("p", "picture", prompt="x"), node("p", "model", engine="sf3d")], [], "twice"),
    ([node("p", "picture", prompt="x")], [("p", "ghost")], "missing node"),
    ([node("p", "picture", prompt="x"), node("q", "picture", prompt="y"), node("m", "model", engine="sf3d")],
     [("p", "m"), ("q", "m")], "one line in"),
    ([node("p", "picture", prompt="x"), node("q", "picture", prompt="y")], [("p", "q")], "takes no line in"),
    ([node("p", "picture", prompt="x"), node("v", "move", move="walk")], [("p", "v")], "Move takes a rigged model"),
    ([node("p", "picture", prompt="x"), node("m", "model")], [("p", "m")], "pick an engine"),
    ([node("p", "picture", prompt="x"), node("m", "model", engine="sf3d"), node("r", "rig"), node("v", "move")],
     [("p", "m"), ("m", "r"), ("r", "v")], "pick a move"),
    ([node("p", "picture", prompt="x", goal="prop"), node("m", "model", engine="sf3d"), node("s", "split")],
     [("p", "m"), ("m", "s")], "sheet of nine"),
    ([node("p", "picture", prompt="x"), node("x", "export")], [("p", "x")], "Export takes a 3D model"),
    ([node("p", "picture", prompt="x", goal="planet")], [], "Picture is for"),
    ([node("p", "picture", file="../etc/passwd")], [], "not a file in output"),
])
def test_a_graph_that_cannot_run_says_why(nodes, edges, words):
    with pytest.raises(ValueError, match=words):
        ga.plan_graph(graph(nodes, edges), RECIPES)


def test_a_loop_is_refused():
    # every node has a line in, so the loop has no Picture to start from; refused all the same
    g = graph([node("p", "picture", prompt="x"), node("m", "model", engine="sf3d"), node("f", "finish"),
               node("r", "rig")], [("p", "m"), ("m", "f")])
    g["edges"].append({"from": "f", "to": "m"})
    with pytest.raises(ValueError, match="one line in|loop"):
        ga.plan_graph(g, RECIPES)


# ---------------------------------------------------------------- per-prop instances (pure)
def test_a_per_prop_stage_runs_once_for_each_prop_of_each_split():
    stage = ga.plan_graph(SHEET, RECIPES).stages[1]
    props = {"props/a": ["props/a/finished/sword/sword_LOD0.glb", "props/a/finished/axe/axe_LOD0.glb"]}
    runs = ga.instances(stage, [{"picture": "p.png", "model": "m.glb", "split": "props/a"}], props.get)
    assert [r["made"] for r in runs] == [{"finished": "props/a/finished/sword/sword_LOD0.glb"},
                                         {"finished": "props/a/finished/axe/axe_LOD0.glb"}]
    assert [r["label"] for r in runs] == ["sword", "axe"]


def test_a_branch_starts_from_everything_its_parent_made():
    plan = ga.plan_graph(graph([node("a", "picture", prompt="a fox", goal="prop"), node("b", "model", engine="sf3d"),
                                node("c", "finish"), node("d", "rig")], [("a", "b"), ("b", "c"), ("b", "d")]), RECIPES)
    runs = ga.instances(plan.stages[1], [{"picture": "p.png", "model": "m.glb"}], lambda _: [])
    assert runs == [{"made": {"picture": "p.png", "model": "m.glb"}, "label": None}]
    first = ga.instances(plan.stages[0], [], lambda _: [])
    assert first == [{"made": {}, "label": None}]


def test_the_props_of_a_split_are_its_finished_lod0_files(tmp_path):
    run = tmp_path / "props" / "swords__props__20261116-101010"
    for name in ("sword", "axe"):
        (run / "finished" / name).mkdir(parents=True)
        (run / "finished" / name / f"{name}_LOD0.glb").write_bytes(b"x")
    (run / "finished" / "half").mkdir()   # a prop whose bake stopped has no LOD0, and is left out
    assert ga.split_props(tmp_path, "props/swords__props__20261116-101010") == [
        "props/swords__props__20261116-101010/finished/axe/axe_LOD0.glb",
        "props/swords__props__20261116-101010/finished/sword/sword_LOD0.glb",
    ]
    assert ga.split_props(tmp_path, "../outside") == []


# ---------------------------------------------------------------- running a graph
class FakeJobs:
    """The job endpoints, each job done on its first poll; a split makes two props on disk."""

    def __init__(self, output: Path, fail: str | None = None):
        self.output, self.fail = output, fail
        self.started: list[str] = []
        self.jobs: dict[str, str] = {}

    def __call__(self, method, path, body, content_type, headers=None):
        if method == "POST" and path.endswith("/cancel"):
            return 200, {"ok": True}
        if method == "POST":
            kind = {"/api/image": "picture", "/api/generate": "model", "/api/finish": "finished",
                    "/api/animate/rig": "rigged", "/api/animate/play": "animated", "/api/props": "split"}[path]
            self.started.append(kind)
            job_id = f"job-{len(self.started)}"
            self.jobs[job_id] = kind
            return 202, {"job_id": job_id, "status_url": f"/api/x/{job_id}/status"}
        kind = self.jobs[path.split("/")[3]]
        if kind == self.fail:
            return 200, {"status": "error", "error": "it broke", "log_tail": ""}
        n = len(self.started)
        made = {"picture": "images/p.png", "model": f"sf3d/m{n}/model.glb", "finished": f"finish/f{n}.glb",
                "rigged": f"animate/r{n}/rigged.glb", "animated": f"animate/r{n}/walk.glb"}
        event = {"phase": "done", "message": "done", "overall_pct": 100}
        if kind == "split":
            folder = self.output / "props" / "s__props__20261116-101010"
            for name in ("axe", "sword"):
                (folder / "finished" / name).mkdir(parents=True, exist_ok=True)
                (folder / "finished" / name / f"{name}_LOD0.glb").write_bytes(b"x")
            event["directory"] = folder.name
        else:
            (self.output / made[kind]).parent.mkdir(parents=True, exist_ok=True)
            (self.output / made[kind]).write_bytes(b"x")
        if kind in ("finished", "rigged", "animated"):
            event["path"] = made[kind]
        return 200, {"status": "done", "last_event": event, "log_tail": "ok",
                     "picture": made["picture"] if kind == "picture" else None,
                     "model": made["model"] if kind == "model" else None}


def _graph_runner(tmp_path, **fake):
    jobs = FakeJobs(tmp_path, **fake)
    chains = aa.ChainRunner(tmp_path, jobs, poll=0, sleep=lambda s: None)
    return ga.GraphRunner(tmp_path, chains, recipes=RECIPES, sleep=lambda s: None), jobs


def test_a_split_records_the_folder_its_props_went_to(tmp_path):
    jobs = FakeJobs(tmp_path)
    runner = aa.ChainRunner(tmp_path, jobs, poll=0, sleep=lambda s: None)
    chain = runner.submit(aa.Chain(title="swords", prompt="x", engine="sf3d", steps=["picture", "model", "split"]),
                          background=False)
    assert chain.made["split"] == "props/s__props__20261116-101010"


def test_a_straight_graph_runs_as_one_chain_and_each_node_shows_its_file(tmp_path):
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(CHARACTER, background=False)
    assert run.status == "done"
    assert jobs.started == ["picture", "model", "finished", "rigged", "animated"]
    assert len(run.chains) == 1
    results = run.to_dict()["results"]
    assert results["n1"] == [{"status": "done", "file": "images/p.png"}]
    assert results["n5"] == [{"status": "done", "file": "animate/r5/walk.glb"}]


def test_a_sheet_fans_out_into_one_rig_per_prop(tmp_path):
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(SHEET, background=False)
    assert run.status == "done"
    assert jobs.started == ["picture", "model", "split", "rigged", "rigged"]
    assert len(run.chains) == 3                             # the sheet, then one short run per prop
    results = run.to_dict()["results"]
    props = [r["file"] for r in results["s3"]]
    assert props == ["props/s__props__20261116-101010/finished/axe/axe_LOD0.glb",
                     "props/s__props__20261116-101010/finished/sword/sword_LOD0.glb"]
    assert [r["label"] for r in results["s4"]] == ["axe", "sword"]
    assert results["s5"] == results["s3"]                   # Export lists what the split made


def test_a_failed_branch_skips_what_hangs_off_it_and_its_sibling_still_runs(tmp_path):
    g = graph([node("a", "picture", prompt="a fox", goal="prop"), node("b", "model", engine="sf3d"),
               node("c", "finish"), node("d", "rig"), node("e", "move", move="walk")],
              [("a", "b"), ("b", "c"), ("b", "d"), ("d", "e")])
    runner, jobs = _graph_runner(tmp_path, fail="finished")
    run = runner.start(g, background=False)
    assert jobs.started == ["picture", "model", "finished", "rigged", "animated"]
    assert run.status == "error"
    results = run.to_dict()["results"]
    assert results["c"][0]["status"] == "error"
    assert results["e"][0]["status"] == "done"


def test_a_failure_marks_the_later_nodes_of_its_run_skipped(tmp_path):
    runner, _ = _graph_runner(tmp_path, fail="model")
    run = runner.start(CHARACTER, background=False)
    results = run.to_dict()["results"]
    assert [results[n][0]["status"] for n in ("n1", "n2", "n3", "n5")] == ["done", "error", "skipped", "skipped"]


def test_a_graph_that_cannot_run_is_refused_before_anything_starts(tmp_path):
    runner, jobs = _graph_runner(tmp_path)
    with pytest.raises(ValueError):
        runner.start(graph([node("m", "model", engine="sf3d")], []), background=False)
    assert jobs.started == []


def test_the_run_is_saved_on_the_graph_so_a_reopened_canvas_shows_it(tmp_path):
    saved = ga.save_graph(tmp_path, {**CHARACTER, "id": None})
    runner, _ = _graph_runner(tmp_path)
    runner.start(saved, background=False)
    last = ga.get_graph(tmp_path, saved["id"])["last_run"]
    assert last["status"] == "done" and last["results"]["n2"][0]["file"] == "sf3d/m2/model.glb"


def test_cancel_stops_the_running_chain_and_skips_the_rest(tmp_path):
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(SHEET, background=False, before_stage=lambda r, i: i == 1 and runner.cancel(r.id))
    assert run.status == "cancelled"
    assert jobs.started == ["picture", "model", "split"]


# ---------------------------------------------------------------- the saved graphs
def test_no_file_means_no_graphs_and_a_broken_one_too(tmp_path):
    assert ga.list_graphs(tmp_path) == []
    (tmp_path / ga.GRAPHS_NAME).write_text("{nope", encoding="utf-8")
    assert ga.list_graphs(tmp_path) == []


def test_saving_gives_a_new_graph_an_id_and_keeps_it_next_time(tmp_path):
    first = ga.save_graph(tmp_path, {"name": "  My   board ", "nodes": [node("n1", "picture", prompt="x")], "edges": []},
                          now=lambda: 10)
    assert ga.GRAPH_ID.fullmatch(first["id"]) and first["name"] == "My board" and first["updated"] == 10
    again = ga.save_graph(tmp_path, {**first, "name": "Renamed"}, now=lambda: 20)
    assert again["id"] == first["id"]
    assert [g["name"] for g in ga.list_graphs(tmp_path)] == ["Renamed"]
    assert json.loads((tmp_path / ga.GRAPHS_NAME).read_text())["graphs"][0]["schema"] == 1


def test_a_half_drawn_graph_saves_but_junk_does_not(tmp_path):
    half = ga.save_graph(tmp_path, {"name": "wip", "nodes": [node("n1", "model")], "edges": [{"from": "n1", "to": "n9"}]})
    assert half["nodes"][0]["type"] == "model"
    with pytest.raises(ValueError):
        ga.save_graph(tmp_path, {"name": "x", "nodes": "lots", "edges": []})
    with pytest.raises(ValueError):
        ga.save_graph(tmp_path, {"name": "x", "nodes": [node(f"n{i}", "picture") for i in range(ga.MAX_NODES + 1)],
                                 "edges": []})


def test_deleting_a_graph_removes_only_the_board(tmp_path):
    g = ga.save_graph(tmp_path, {"name": "x", "nodes": [], "edges": []})
    ga.delete_graph(tmp_path, g["id"])
    assert ga.list_graphs(tmp_path) == []
    with pytest.raises(ValueError):
        ga.delete_graph(tmp_path, "graph-zzzzzzzz")


def test_the_canvas_routes_end_to_end(tmp_path, monkeypatch):
    """Save, check and run a canvas through the real server, with fake jobs underneath."""
    import http.server
    import threading
    import urllib.error
    import urllib.request

    import generate_api as api

    monkeypatch.setattr(api, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(api, "find_blender", lambda: Path("/Applications/Blender.app"))
    runner, jobs = _graph_runner(tmp_path)
    monkeypatch.setattr(api.Handler, "_graph_runner", runner)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/api"

    def call(url, body=None):
        request = urllib.request.Request(f"{base}/{url}", data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", "X-AssetFurnace-Request": "studio"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    try:
        assert call("graphs") == (200, {"graphs": []})
        status, saved = call("graphs", {**SHEET, "id": None})
        assert status == 200 and ga.GRAPH_ID.fullmatch(saved["id"])
        assert call(f"graphs/{saved['id']}")[1]["name"] == "Swords"
        assert call("graphs/check", SHEET)[1]["plan"][1].startswith("Run 2, once for each prop")
        assert call("graphs/check", {"nodes": [], "edges": []})[0] == 422
        status, run = call(f"graphs/{saved['id']}/run", {})
        assert status == 202
        for _ in range(200):
            status, run = call(f"graph-runs/{run['id']}")
            if run["status"] != "running":
                break
            threading.Event().wait(0.02)
        assert run["status"] == "done" and len(run["results"]["s4"]) == 2
        assert jobs.started == ["picture", "model", "split", "rigged", "rigged"]
        assert call(f"graphs/{saved['id']}")[1]["last_run"]["status"] == "done"
        assert call(f"graphs/{saved['id']}/delete", {})[1]["deleted"] is True
        assert call("graphs/graph-00000000/run", {})[0] == 422
    finally:
        server.shutdown()
        server.server_close()
