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
    ([node("p", "picture", prompt="x")], [("p", "ghost")], "missing box"),
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
    ([node("p", "picture", file="../etc/passwd")], [], "describe it"),   # a path outside output/ is dropped
    ([node("a", "asset")], [], "pick one from the Library"),
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
    runs = ga.instances(stage, [{"made": {"picture": "p.png", "model": "m.glb", "split": "props/a"}, "label": None}], props.get)
    assert [r["made"] for r in runs] == [{"finished": "props/a/finished/sword/sword_LOD0.glb"},
                                         {"finished": "props/a/finished/axe/axe_LOD0.glb"}]
    assert [r["label"] for r in runs] == ["sword", "axe"]


def test_a_branch_starts_from_everything_its_parent_made():
    plan = ga.plan_graph(graph([node("a", "picture", prompt="a fox", goal="prop"), node("b", "model", engine="sf3d"),
                                node("c", "finish"), node("d", "rig")], [("a", "b"), ("b", "c"), ("b", "d")]), RECIPES)
    runs = ga.instances(plan.stages[1], [{"made": {"picture": "p.png", "model": "m.glb"}, "label": None}], lambda _: [])
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
        self.parts: list[str] = []
        self.bodies: list[dict] = []
        self.jobs: dict[str, str] = {}

    def __call__(self, method, path, body, content_type, headers=None):
        if method == "POST" and path.endswith("/cancel"):
            return 200, {"ok": True}
        if method == "POST":
            kind = {"/api/image": "picture", "/api/image/back": "back", "/api/generate": "model", "/api/finish": "finished",
                    "/api/animate/rig": "rigged", "/api/animate/play": "animated", "/api/props": "split",
                    "/api/image/part": "part", "/api/tool/cutout": "cutout", "/api/tool/remesh": "remesh",
                    "/api/tool/unwrap": "unwrap", "/api/tool/export": "export", "/api/tool/combine": "combine",
                    "/api/tool/mirror": "mirror"}[path]
            self.started.append(kind)
            if kind in ("cutout", "remesh", "unwrap", "export", "combine", "mirror"):
                self.bodies.append({"tool": kind, **json.loads(body)})
            if kind == "part":
                self.parts.append(json.loads(body)["part"])
            if path == "/api/image/back":
                self.bodies.append(json.loads(body))
            if path == "/api/generate":
                self.bodies.append({"generate": body})
            job_id = f"job-{len(self.started)}"
            self.jobs[job_id] = kind
            return 202, {"job_id": job_id, "status_url": f"/api/x/{job_id}/status"}
        kind = self.jobs[path.split("/")[3]]
        if kind == self.fail:
            return 200, {"status": "error", "error": "it broke", "log_tail": ""}
        n = len(self.started)
        slug = ga.slugged(self.parts[-1]) if self.parts else ""
        made = {"picture": f"images/{slug}__part__{n}/{slug}__part__{n}.png" if kind == "part"
                else f"images/p__side{n}.png" if kind == "back" else "images/p.png",
                "model": f"sf3d/m{n}/model.glb", "finished": f"finish/f{n}.glb",
                "rigged": f"animate/r{n}/rigged.glb", "animated": f"animate/r{n}/walk.glb"}
        event = {"phase": "done", "message": "done", "overall_pct": 100}
        if kind in ("cutout", "remesh", "unwrap", "export", "combine", "mirror"):
            file = {"cutout": f"images/cut{n}/cut{n}.png", "export": f"exports/e{n}/e{n}.fbx",
                    "mirror": f"images/p__side{n}.png"}.get(kind, f"tools/t{n}/t{n}.glb")
            (self.output / file).parent.mkdir(parents=True, exist_ok=True)
            (self.output / file).write_bytes(b"x")
            key = {"cutout": "picture", "mirror": "picture", "export": "file"}.get(kind, "model")
            return 200, {"status": "done", "last_event": {"phase": kind, "message": "done"}, "log_tail": "ok", key: file}
        if kind == "split":
            folder = self.output / "props" / "s__props__20261116-101010"
            for name in ("axe", "sword"):
                (folder / "finished" / name).mkdir(parents=True, exist_ok=True)
                (folder / "finished" / name / f"{name}_LOD0.glb").write_bytes(b"x")
            event["directory"] = folder.name
        else:
            file = made["picture" if kind in ("part", "back") else kind]
            (self.output / file).parent.mkdir(parents=True, exist_ok=True)
            (self.output / file).write_bytes(b"x")
        if kind in ("finished", "rigged", "animated"):
            event["path"] = made[kind]
        return 200, {"status": "done", "last_event": event, "log_tail": "ok",
                     "picture": made["picture"] if kind in ("picture", "part", "back") else None,
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
    assert json.loads((tmp_path / ga.GRAPHS_NAME).read_text())["graphs"][0]["schema"] == 2


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


# ---------------------------------------------------------------- round two: settings, picks, one box at a time
def test_a_box_keeps_only_settings_that_fit_and_schema_1_still_reads():
    old = {"schema": 1, "name": "old", "nodes": [node("f", "finish", detail="more", texture_size="1024", pixel_match="false",
                                                      height_m="999", cleanup="sideways", junk=1)], "edges": []}
    g = ga.clean_graph(old)
    assert g["schema"] == 2 and g["groups"] == []
    assert g["nodes"][0]["settings"] == {"detail": "more", "texture_size": 1024, "pixel_match": False}


def test_groups_picks_and_the_view_are_kept_and_junk_is_dropped():
    g = ga.clean_graph({"name": "x", "nodes": [node("a", "picture", prompt="x"), node("b", "model", engine="sf3d")],
                        "edges": [], "groups": [{"id": "g1", "label": "  Head  ", "nodes": ["a", "ghost"]},
                                                {"id": "g2", "nodes": ["ghost"]}, {"id": "bad", "nodes": ["a"]}],
                        "view": {"x": 10, "y": -5, "k": 9}})
    g2 = ga.clean_graph({**g, "nodes": [{**g["nodes"][0], "picked": "images/a.png"}, {**g["nodes"][1], "picked": "/etc/x"}]})
    assert g["groups"] == [{"id": "g1", "label": "Head", "nodes": ["a"]}]
    assert g["view"] == {"x": 10.0, "y": -5.0, "k": 2.5}
    assert g2["nodes"][0]["picked"] == "images/a.png" and "picked" not in g2["nodes"][1]


def test_a_save_from_a_stale_copy_is_refused(tmp_path):
    first = ga.save_graph(tmp_path, {"name": "x", "nodes": [], "edges": []}, now=lambda: 10)
    ga.save_graph(tmp_path, {**first, "name": "agent's edit"}, now=lambda: 10)   # same second: still a new version
    with pytest.raises(ga.Stale):
        ga.save_graph(tmp_path, {**first, "name": "the tab's old copy"}, now=lambda: 11)
    assert ga.list_graphs(tmp_path)[0]["name"] == "agent's edit"


def test_box_settings_reach_the_run_as_create_would_send_them():
    g = graph([node("p", "picture", prompt="a lamp", goal="prop", shape="tall", picture_engine="qwen-image"),
               node("m", "model", engine="sf3d"),
               node("f", "finish", detail="hero", pixel_match=False, texture_size=512, surface_maps=False, cleanup="fast"),
               node("r", "rig", bone_names="unity"), node("v", "move", move="walk", speed=1.5, in_place=False)],
              [("p", "m"), ("m", "f"), ("f", "r"), ("r", "v")])
    stage = ga.plan_graph(g, RECIPES).stages[0]
    assert stage.picture_settings == {"width": 832, "height": 1216} and stage.picture_engine == "qwen-image"
    assert stage.finish_settings == {"upright": True, "faces": 32000, "hero": True, "skip_photo": True,
                                     "texture_size": 512, "drop_surface_maps": True, "cleanup": "fast"}
    assert stage.rig_settings == {"bone_names": "unity"}
    assert stage.move_settings == {"speed": 1.5, "in_place": False}


def test_split_names_go_to_the_props_job_one_per_line():
    g = graph([node("s1", "picture", prompt="a knight's parts", goal="set"), node("s2", "model", engine="sf3d"),
               node("s3", "split", names="head, left hand,\nhelmet")], [("s1", "s2"), ("s2", "s3")])
    assert ga.plan_graph(g, RECIPES).stages[0].split_settings == {"names": "head\nleft-hand\nhelmet"}


def test_a_split_sends_its_names(tmp_path):
    jobs = FakeJobs(tmp_path)
    sent = []
    runner = aa.ChainRunner(tmp_path, lambda *a, **k: (sent.append(a), jobs(*a, **k))[1], poll=0, sleep=lambda s: None)
    runner.submit(aa.Chain(title="x", steps=["split"], made={"model": "m.glb"}, split_settings={"names": "a\nb", "junk": 1}),
                  background=False)
    body = next(a[2] for a in sent if a[1] == "/api/props").decode()
    assert '{"names": "a\\nb"}' in body


def test_your_own_3d_model_starts_a_run_without_a_picture():
    g = graph([node("a", "asset", file="sf3d/m/model.glb", goal="prop"), node("f", "finish")], [("a", "f")])
    stage = ga.plan_graph(g, RECIPES).stages[0]
    assert stage.steps == ["finished"] and stage.made == {"model": "sf3d/m/model.glb"} and stage.want == "prop"


def test_front_and_back_sits_between_the_picture_and_the_3d_model():
    g = graph([node("p", "picture", prompt="a knight"), node("b", "back"), node("m", "model", engine="pixal3d")],
              [("p", "b"), ("b", "m")])
    assert ga.plan_graph(g, RECIPES).stages[0].steps == ["picture", "back", "model"]
    with pytest.raises(ValueError, match="Front \\+ back takes a picture"):
        ga.plan_graph(graph([node("p", "picture", prompt="x"), node("m", "model", engine="sf3d"), node("b", "back")],
                            [("p", "m"), ("m", "b")]), RECIPES)


def test_a_picture_with_options_runs_each_and_what_follows_waits_for_a_pick(tmp_path):
    g = graph([node("p", "picture", prompt="a lamp", goal="prop", count=3), node("m", "model", engine="sf3d"),
               node("f", "finish")], [("p", "m"), ("m", "f")])
    plan = ga.plan_graph(g, RECIPES)
    assert [s.steps for s in plan.stages] == [["picture"], ["model", "finished"]]
    assert ga.describe_plan(plan)[0] == "Run 1: Picture (3 options to pick from)"
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(g, background=False)
    assert jobs.started == ["picture", "picture", "picture"] and run.status == "done"
    out = run.to_dict()
    assert [r["label"] for r in out["results"]["p"]] == ["Option 1", "Option 2", "Option 3"]
    assert out["results"]["m"] == [{"status": "waiting"}] and out["waiting_for_pick"] == ["p"]


def test_after_a_pick_the_boxes_below_run_from_the_picked_picture(tmp_path):
    g = graph([node("p", "picture", prompt="a lamp", goal="prop", count=2), node("m", "model", engine="sf3d"),
               node("f", "finish")], [("p", "m"), ("m", "f")])
    results = {"p": [{"status": "done", "file": "images/a.png", "label": "Option 1"},
                     {"status": "done", "file": "images/b.png", "label": "Option 2"}]}
    with pytest.raises(ValueError, match="Pick one of Picture's 2 results first"):
        ga.plan_from(g, "p", "after", results, RECIPES)
    g["nodes"][0]["picked"] = "images/b.png"
    plan = ga.plan_from(g, "p", "after", results, RECIPES)
    assert [(s.steps, s.seeds) for s in plan.stages] == [(["model", "finished"], [{"made": {"picture": "images/b.png"}, "label": None}])]


def test_one_box_runs_from_what_its_parents_made_last_time():
    results = {"n1": [{"status": "done", "file": "images/k.png"}], "n2": [{"status": "done", "file": "m/k.glb"}],
               "n3": [{"status": "done", "file": "finish/k.glb"}]}
    only = ga.plan_from(CHARACTER, "n3", "only", results, RECIPES)
    assert [(s.steps, s.seeds) for s in only.stages] == [(["finished"], [{"made": {"picture": "images/k.png", "model": "m/k.glb"}, "label": None}])]
    below = ga.plan_from(CHARACTER, "n3", "from", results, RECIPES)
    assert below.stages[0].steps == ["finished", "rigged", "animated"]
    with pytest.raises(ValueError, match="Run Finish first"):
        ga.plan_from(CHARACTER, "n4", "only", {k: v for k, v in results.items() if k != "n3"}, RECIPES)
    assert ga.plan_from(CHARACTER, "n1", "only", {}, RECIPES).stages[0].steps == ["picture"]


def test_one_box_below_a_split_runs_once_per_prop_that_got_that_far():
    results = {"s3": [{"status": "done", "file": "props/a/axe_LOD0.glb", "label": "axe"},
                      {"status": "done", "file": "props/a/bow_LOD0.glb", "label": "bow"}]}
    plan = ga.plan_from(SHEET, "s4", "only", results, RECIPES)
    assert plan.stages[0].seeds == [{"made": {"finished": "props/a/axe_LOD0.glb"}, "label": "axe"},
                                    {"made": {"finished": "props/a/bow_LOD0.glb"}, "label": "bow"}]
    assert plan.stages[0].want == "prop"
    assert ga.describe_plan(plan) == ["Run 1, once for each of 2 props: Rig"]


def test_the_boxes_above_one_box_run_on_its_own_only_hand_on_what_they_made():
    blank = graph([node("p", "picture"), node("m", "model"), node("f", "finish")], [("p", "m"), ("m", "f")])
    with pytest.raises(ValueError, match="Run Picture first"):     # not "describe it": the Picture does not run
        ga.plan_from(blank, "f", "only", {}, RECIPES)
    results = {"p": [{"status": "done", "file": "images/p.png"}], "m": [{"status": "done", "file": "m.glb"}]}
    assert ga.plan_from(blank, "f", "only", results, RECIPES).stages[0].steps == ["finished"]


def test_running_one_box_ignores_a_half_drawn_branch_elsewhere():
    g = graph([node("p", "picture", prompt="x"), node("m", "model", engine="sf3d"), node("v", "move")],
              [("p", "m"), ("p", "v")])   # the Move is wrong, and not part of this run
    with pytest.raises(ValueError):
        ga.plan_graph(g, RECIPES)
    plan = ga.plan_from(g, "m", "only", {"p": [{"status": "done", "file": "images/x.png"}]}, RECIPES)
    assert plan.stages[0].steps == ["model"]


def test_running_one_box_keeps_what_the_other_boxes_made(tmp_path):
    saved = ga.save_graph(tmp_path, {**CHARACTER, "id": None})
    runner, jobs = _graph_runner(tmp_path)
    runner.start(saved, background=False)
    again = ga.get_graph(tmp_path, saved["id"])
    run = runner.start(again, node="n3", mode="only", background=False)
    assert jobs.started[-1:] == ["finished"] and run.status == "done"
    last = ga.get_graph(tmp_path, saved["id"])["last_run"]["results"]
    assert last["n1"] == [{"status": "done", "file": "images/p.png"}]       # kept from the first run
    assert last["n3"] == [{"status": "done", "file": "finish/f6.glb"}]      # this run's own


def test_a_dropped_picture_is_kept_under_uploads_and_anything_else_refused(tmp_path):
    import io

    from PIL import Image

    data = io.BytesIO()
    Image.new("RGB", (8, 8)).save(data, "PNG")
    kept = ga.keep_upload(tmp_path, "My Orc!.png", data.getvalue())
    assert kept.startswith("images/uploads/my-orc__") and (tmp_path / kept).read_bytes() == data.getvalue()
    for name, body in (("x.exe", data.getvalue()), ("x.png", b"not a picture"), ("x.png", b"")):
        with pytest.raises(ValueError):
            ga.keep_upload(tmp_path, name, body)


def test_running_one_box_and_a_stale_save_through_the_server(tmp_path, monkeypatch):
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
        saved = call("graphs", {**CHARACTER, "id": None})[1]
        assert call(f"graphs/{saved['id']}/run", {"node": "n3", "mode": "only"}) [0] == 422   # nothing made yet
        assert call("graphs/check", {"graph": saved, "node": "n1", "mode": "only"})[1] == {"plan": ["Run 1: Picture"]}
        status, run = call(f"graphs/{saved['id']}/run", {"node": "n1", "mode": "only"})
        assert status == 202
        for _ in range(200):
            run = call(f"graph-runs/{run['id']}")[1]
            if run["status"] != "running":
                break
            threading.Event().wait(0.02)
        assert jobs.started == ["picture"]
        newer = call("graphs", {**saved, "name": "renamed"})[1]
        status, refused = call("graphs", {**saved, "name": "old copy"})
        assert status == 409 and refused["stale"] is True
        assert call(f"graphs/{saved['id']}")[1]["name"] == newer["name"] == "renamed"
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------- tries: Again, and a try dragged onto a line
def test_each_line_carries_the_try_dragged_onto_it():
    g = graph([node("p", "picture", prompt="a lamp", goal="prop"), node("m1", "model", engine="sf3d"),
               node("m2", "model", engine="pixal3d")], [("p", "m1"), ("p", "m2")])
    g["edges"][0]["file"], g["edges"][1]["file"] = "images/a.png", "images/c.png"
    results = {"p": [{"status": "done", "file": f"images/{x}.png"} for x in "abc"]}
    one = ga.plan_from(g, "m1", "only", results, RECIPES).stages[0].seeds
    two = ga.plan_from(g, "m2", "only", results, RECIPES).stages[0].seeds
    after = [s.seeds[0]["made"]["picture"] for s in ga.plan_from(g, "p", "after", results, RECIPES).stages]
    assert one[0]["made"] == {"picture": "images/a.png"} and two[0]["made"] == {"picture": "images/c.png"}
    assert after == ["images/a.png", "images/c.png"]
    assert ga.clean_graph({**g, "edges": [{"from": "p", "to": "m1", "file": "../x.png"}]})["edges"] == [{"from": "p", "to": "m1"}]


def test_a_line_with_no_try_on_it_uses_the_box_pick_then_asks():
    g = graph([node("p", "picture", prompt="a lamp", goal="prop"), node("m", "model", engine="sf3d")], [("p", "m")])
    results = {"p": [{"status": "done", "file": "images/a.png"}, {"status": "done", "file": "images/b.png"}]}
    with pytest.raises(ValueError, match="Pick one"):
        ga.plan_from(g, "m", "only", results, RECIPES)
    g["edges"][0]["file"] = "images/gone.png"            # a try that was since replaced: the box's pick decides
    g["nodes"][0]["picked"] = "images/b.png"
    assert ga.plan_from(g, "m", "only", results, RECIPES).stages[0].seeds[0]["made"] == {"picture": "images/b.png"}


def test_again_adds_a_try_and_keeps_the_old_ones(tmp_path):
    saved = ga.save_graph(tmp_path, {**CHARACTER, "id": None})
    runner, _jobs = _graph_runner(tmp_path)
    runner.start(saved, background=False)
    first = ga.get_graph(tmp_path, saved["id"])["last_run"]["results"]["n3"]
    run = runner.start(ga.get_graph(tmp_path, saved["id"]), node="n3", mode="only", again=True, background=False)
    assert run.to_dict()["again"] is True
    tries = ga.get_graph(tmp_path, saved["id"])["last_run"]["results"]["n3"]
    assert tries[0] == first[0] and len(tries) == 2 and tries[1]["file"] != first[0]["file"]
    runner.start(ga.get_graph(tmp_path, saved["id"]), node="n3", mode="only", background=False)   # not again: replaced
    assert len(ga.get_graph(tmp_path, saved["id"])["last_run"]["results"]["n3"]) == 1


def test_one_prop_dragged_onto_a_line_below_a_split_runs_for_that_prop_alone(tmp_path):
    g = json.loads(json.dumps(SHEET))
    g["edges"][2]["file"] = "props/old-run/finished/sword/sword_LOD0.glb"   # s3 -> s4 carries the sword
    results = {"s3": [{"status": "done", "file": f"props/old-run/finished/{n}/{n}_LOD0.glb", "label": n} for n in ("axe", "sword")]}
    seeds = ga.plan_from(g, "s4", "only", results, RECIPES).stages[0].seeds
    assert seeds == [{"made": {"finished": "props/old-run/finished/sword/sword_LOD0.glb"}, "label": "sword"}]
    assert ga.plan_graph(g, RECIPES).stages[1].only_prop == "sword"
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(g, background=False)           # Run all splits again: the sword is still the sword
    assert jobs.started == ["picture", "model", "split", "rigged"]
    assert [r["label"] for r in run.to_dict()["results"]["s4"]] == ["sword"]


# ---------------------------------------------------------------- Pick out parts (element extraction)
PARTS = graph(
    [node("p", "picture", file="images/knight.png", goal="character"), node("x", "parts", names="helmet, left hand,\nhelmet"),
     node("m", "model", engine="sf3d"), node("f", "finish")],
    [("p", "x"), ("x", "m"), ("m", "f")], name="Knight parts")


def test_pick_out_parts_runs_once_per_part_and_each_part_carries_on_alone():
    plan = ga.plan_graph(PARTS, RECIPES)
    given, parts, below = plan.stages
    assert given.steps == [] and given.made == {"picture": "images/knight.png"}
    assert parts.steps == ["part"] and parts.parts == ["helmet", "left hand"] and parts.after == given.id
    assert below.steps == ["model", "finished"] and below.after == parts.id
    assert ga.describe_plan(plan) == ["Starts from Picture you gave", "Run 1, once for each of 2 parts: Pick out parts (helmet, left hand)",
                                      "Run 2, once for each part from run 1: 3D model → Finish"]
    with pytest.raises(ValueError, match="name the parts"):
        ga.plan_graph(graph([node("p", "picture", prompt="x"), node("x", "parts")], [("p", "x")]), RECIPES)


def test_a_parts_run_makes_each_part_then_a_3d_model_of_each(tmp_path):
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(PARTS, background=False)
    assert run.status == "done"
    assert jobs.started == ["part", "part", "model", "finished", "model", "finished"]
    assert jobs.parts == ["helmet", "left hand"]
    results = run.to_dict()["results"]
    assert [(r["label"], r["file"].split("/")[1]) for r in results["x"]] == [("helmet", "helmet__part__1"), ("left hand", "left-hand__part__2")]
    assert [r["label"] for r in results["m"]] == ["helmet", "left hand"]
    assert results["p"] == [{"status": "done", "file": "images/knight.png"}]


def test_one_part_dragged_onto_the_line_carries_on_alone(tmp_path):
    g = json.loads(json.dumps(PARTS))
    g["edges"][1]["file"] = "images/left-hand__part__old/left-hand__part__old.png"
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(g, background=False)                      # Run all: still only the left hand goes on
    assert jobs.started == ["part", "part", "model", "finished"]
    assert [r["label"] for r in run.to_dict()["results"]["m"]] == ["left hand"]
    results = {"x": [{"status": "done", "file": "images/helmet__part__1/helmet__part__1.png", "label": "helmet"},
                     {"status": "done", "file": "images/left-hand__part__2/left-hand__part__2.png", "label": "left hand"}]}
    seeds = ga.plan_from(g, "m", "only", results, RECIPES).stages[0].seeds
    assert seeds == [{"made": {"picture": "images/left-hand__part__2/left-hand__part__2.png"}, "label": "left hand"}]


def test_the_parts_box_run_on_its_own_starts_from_its_picture():
    plan = ga.plan_from(PARTS, "x", "only", {}, RECIPES)
    stage = plan.stages[0]
    assert stage.steps == ["part"] and stage.seeds == [{"made": {"picture": "images/knight.png"}, "label": None}]
    assert [j["part"] for j in ga.instances(stage, [], lambda _: [])] == ["helmet", "left hand"]


def test_a_part_step_asks_for_the_part_by_name_and_hands_its_picture_on(tmp_path):
    jobs = FakeJobs(tmp_path)
    sent = []
    runner = aa.ChainRunner(tmp_path, lambda *a, **k: (sent.append(a), jobs(*a, **k))[1], poll=0, sleep=lambda s: None)
    chain = runner.submit(aa.Chain(title="x", steps=["part", "model"], engine="sf3d", part_name="left hand",
                                   picture_settings={"seed": 9}, made={"picture": "images/knight.png"}), background=False)
    asked = json.loads(next(a[2] for a in sent if a[1] == "/api/image/part"))
    assert asked == {"picture": "images/knight.png", "part": "left hand", "prefer_engine": None, "seed": 9}
    assert chain.made["picture"] == "images/left-hand__part__1/left-hand__part__1.png"   # the 3D model is made from the part
    refused = runner.submit(aa.Chain(title="x", steps=["part"], made={}), background=False)
    assert refused.status == "error" and "needs a picture" in refused.error     # nothing to pick parts from


# ---------------------------------------------------------------- Repaint (re-texture)
def test_a_repaint_paints_the_model_above_it_from_the_picture_and_hands_the_new_one_on(tmp_path):
    g = graph([node("p", "picture", prompt="a knight"), node("m", "model", engine="sf3d"), node("f", "finish"),
               node("r", "repaint"), node("g", "rig")], [("p", "m"), ("m", "f"), ("f", "r"), ("r", "g")])
    assert ga.plan_graph(g, RECIPES).stages[0].steps == ["picture", "model", "finished", "repaint", "rigged"]
    jobs = FakeJobs(tmp_path)
    sent = []
    runner = aa.ChainRunner(tmp_path, lambda *a, **k: (sent.append(a), jobs(*a, **k))[1], poll=0, sleep=lambda s: None)
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "k.png").write_bytes(b"png")
    chain = runner.submit(aa.Chain(title="x", steps=["repaint", "rigged"], model_seed=5,
                                   made={"picture": "images/k.png", "model": "m/raw.glb", "finished": "finish/k_20k.glb"}),
                          background=False)
    body = next(a[2] for a in sent if a[1] == "/api/generate").decode()
    assert '"backend": "hunyuan-paint"' in body and '"mesh": "finish/k_20k.glb"' in body and "k.png" in body
    assert chain.status == "done" and chain.made["model"].endswith("model.glb") and "finished" not in chain.made
    rig = next(a[2] for a in sent if a[1] == "/api/animate/rig").decode()
    assert chain.made["model"] in rig                 # the rig takes the repainted model, not the old finished one


def test_a_repaint_with_no_picture_on_its_line_must_be_given_one():
    g = graph([node("a", "asset", file="m/k.glb"), node("r", "repaint")], [("a", "r")])
    with pytest.raises(ValueError, match="Repaint: pick a picture"):
        ga.plan_graph(g, RECIPES)
    g["nodes"][1]["settings"]["file"] = "images/new-look.png"
    stage = ga.plan_graph(g, RECIPES).stages[0]
    assert stage.steps == ["repaint"] and stage.paint_from == "images/new-look.png"
    below_split = graph([node("s1", "picture", prompt="nine swords", goal="set"), node("s2", "model", engine="sf3d"),
                         node("s3", "split"), node("r", "repaint")], [("s1", "s2"), ("s2", "s3"), ("s3", "r")])
    with pytest.raises(ValueError, match="Repaint: pick a picture"):
        ga.plan_graph(below_split, RECIPES)


# ---------------------------------------------------------------- tool boxes: cut out, remesh, unwrap, save as, put together
def test_tool_boxes_run_in_line_each_from_what_the_box_before_made(tmp_path):
    g = graph([node("p", "picture", prompt="a lamp", goal="prop"), node("c", "cutout"), node("m", "model", engine="sf3d"),
               node("r", "remesh", faces=8000, size=1024), node("u", "unwrap", size=2048), node("s", "saveas", format="obj")],
              [("p", "c"), ("c", "m"), ("m", "r"), ("r", "u"), ("u", "s")])
    assert ga.plan_graph(g, RECIPES).stages[0].steps == ["picture", "cutout", "model", "remesh", "unwrap", "export"]
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(g, background=False)
    assert run.status == "done", run.error
    cut, remesh, unwrap, export = [{k: v for k, v in b.items() if k != "tool"} for b in jobs.bodies if "generate" not in b]
    assert cut == {"picture": "images/p.png"}
    assert remesh == {"model": "sf3d/m3/model.glb", "faces": 8000, "size": 1024}
    assert unwrap == {"model": "tools/t4/t4.glb", "size": 2048}            # unwraps what the remesh made
    assert export == {"model": "tools/t5/t5.glb", "format": "obj"}
    results = run.to_dict()["results"]
    assert results["c"][0]["file"] == "images/cut2/cut2.png" and results["s"][0]["file"] == "exports/e6/e6.fbx"


def test_put_together_gathers_every_part_into_one_run(tmp_path):
    g = graph([node("p", "picture", file="images/knight.png"), node("x", "parts", names="helmet, shield, cape"),
               node("m", "model", engine="sf3d"), node("t", "combine")], [("p", "x"), ("x", "m"), ("m", "t")])
    plan = ga.plan_graph(g, RECIPES)
    assert [s.steps for s in plan.stages] == [[], ["part"], ["model"], ["combine"]]
    assert ga.describe_plan(plan)[-1] == "Run 3, once with everything from run 2: Put together"
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(g, background=False)
    assert run.status == "done", run.error
    assert jobs.started == ["part", "part", "part", "model", "model", "model", "combine"]
    assert jobs.bodies[-1] == {"tool": "combine", "models": ["sf3d/m4/model.glb", "sf3d/m5/model.glb", "sf3d/m6/model.glb"]}
    assert len(run.to_dict()["results"]["t"]) == 1


def test_put_together_after_a_split_takes_every_prop(tmp_path):
    g = graph([node("s1", "picture", prompt="nine swords", goal="set"), node("s2", "model", engine="sf3d"),
               node("s3", "split"), node("t", "combine")], [("s1", "s2"), ("s2", "s3"), ("s3", "t")])
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(g, background=False)
    assert run.status == "done", run.error
    assert jobs.bodies[-1] == {"tool": "combine", "models": ["props/s__props__20261116-101010/finished/axe/axe_LOD0.glb",
                                          "props/s__props__20261116-101010/finished/sword/sword_LOD0.glb"]}


def test_a_dropped_glb_is_kept_where_the_library_finds_models_and_anything_else_refused(tmp_path):
    kept = ga.keep_model_upload(tmp_path, "My Knight.glb", b"glTF" + b"\0" * 20)
    folder = kept.split("/")[1]
    assert kept == f"uploads/{folder}/{folder}.glb" and folder.startswith("my-knight__")
    assert (tmp_path / kept).read_bytes().startswith(b"glTF")
    for name, body in (("x.fbx", b"glTF"), ("x.glb", b"not a glb"), ("x.glb", b"")):
        with pytest.raises(ValueError):
            ga.keep_model_upload(tmp_path, name, body)


# ---------------------------------------------------------------- Four views (Hunyuan3D-2mv)
FOUR = graph([node("p", "picture", prompt="a knight"), node("v", "views"), node("m", "model", engine="hunyuan_xiong")],
             [("p", "v"), ("v", "m")])


def test_four_views_draws_three_sides_and_the_3d_model_reads_all_four(tmp_path):
    stage = ga.plan_graph(FOUR, RECIPES).stages[0]
    assert stage.steps == ["picture", "left", "back", "right", "model"] and stage.four_views
    runner, jobs = _graph_runner(tmp_path)
    run = runner.start(FOUR, background=False)
    assert run.status == "done", run.error
    sides = [b for b in jobs.bodies if "picture" in b]
    assert [b.get("tool") or b.get("side", "back") for b in sides] == ["left", "back", "mirror"]   # the right is the left mirrored
    assert sides[2]["picture"] == "images/p__side2.png"                                              # the left view
    generate = next(b["generate"] for b in jobs.bodies if "generate" in b).decode(errors="replace")
    assert '"model": "2mv"' in generate
    for side, n in (("left", 2), ("back", 3), ("right", 4)):
        assert f'name="{side}"; filename="p__side{n}.png"' in generate
    assert [r["label"] for r in run.to_dict()["results"]["v"]] == ["left view", "back view", "right view"]


def test_four_views_need_the_engine_with_hunyuan3d_2mv():
    g = json.loads(json.dumps(FOUR))
    g["nodes"][2]["settings"]["engine"] = "pixal3d"
    with pytest.raises(ValueError, match="four views need the Fast and clean engine"):
        ga.plan_graph(g, RECIPES)


def test_a_3d_model_run_on_its_own_after_four_views_takes_every_side():
    results = {"p": [{"status": "done", "file": "images/k.png"}],
               "v": [{"status": "done", "file": f"images/k__{s}.png", "label": f"{s} view"} for s in ("left", "back", "right")]}
    stage = ga.plan_from(FOUR, "m", "only", results, RECIPES).stages[0]
    assert stage.four_views and stage.seeds == [{"made": {"picture": "images/k.png", "left": "images/k__left.png",
                                                          "back": "images/k__back.png", "right": "images/k__right.png"},
                                                 "label": None}]
