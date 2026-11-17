"""Agents build and run canvases (image_to_3dlab/mcp_server.py canvas tools), against the real studio
routes with fake jobs underneath (tests/test_viewer_graph_api.py's FakeJobs)."""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from image_to_3dlab import mcp_server as ms

pytest.importorskip("requests")


def test_operations_build_a_canvas_and_each_bad_one_says_which():
    g = ms.apply_ops({"name": "x", "nodes": [], "edges": []}, [
        {"add": "picture", "id": "p", "settings": {"prompt": "a lamp", "goal": "prop"}},
        {"add": "model", "after": "p", "settings": {"engine": "sf3d"}},
        {"add": "model", "after": "p", "settings": {"engine": "pixal3d"}},
        {"add": "finish", "after": "n2"},
        {"set": "n2", "settings": {"engine": "trellis"}}, {"set": "p", "settings": {"goal": None}},
        {"disconnect": ["p", "n3"]}, {"connect": ["p", "n3"], "file": "images/b.png"}, {"pick": "p", "file": "images/a.png"},
        {"remove": "n3"}, {"rename": "Lamp"},
    ])
    assert [(n["id"], n["type"], n["x"], n["y"]) for n in g["nodes"]] == [("p", "picture", 40, 60), ("n2", "model", 300, 60),
                                                                        ("n4", "finish", 560, 60)]
    assert g["edges"] == [{"from": "p", "to": "n2"}, {"from": "n2", "to": "n4"}]   # n3 went, with its line
    assert g["nodes"][0]["settings"] == {"prompt": "a lamp"} and g["nodes"][0]["picked"] == "images/a.png"
    assert g["nodes"][1]["settings"] == {"engine": "trellis"} and g["name"] == "Lamp"
    for bad, words in (({"add": "model", "after": "ghost"}, "no box ghost"), ({"remove": "zz"}, "no box zz"),
                       ({"connect": ["p"]}, "two box ids"), ({"teleport": 1}, "use add, remove"),
                       ({"add": "picture", "id": "p"}, "already a box p")):
        with pytest.raises(ms.StudioError, match=words):
            ms.apply_ops(g, [bad])


@pytest.fixture
def studio(tmp_path, monkeypatch):
    import generate_api as api
    from test_viewer_graph_api import _graph_runner

    monkeypatch.setattr(api, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(api, "find_blender", lambda: Path("/Applications/Blender.app"))
    runner, jobs = _graph_runner(tmp_path)
    monkeypatch.setattr(api.Handler, "_graph_runner", runner)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    studio = ms.Studio(f"http://127.0.0.1:{server.server_port}", start=lambda: None)
    studio._checked = True       # the catalog route reads this machine's engines; not needed here
    studio.jobs = jobs
    yield studio
    server.shutdown()
    server.server_close()


def test_an_agent_builds_checks_runs_and_reads_a_canvas(studio):
    types = ms.box_types(studio)["types"]
    assert types["model"]["takes"] == ["picture", "pair", "views"] and types["finish"]["settings"]["detail"]["one_of"][0] == "light"
    made = ms.edit_canvas(studio, name="Swords", ops=[
        {"add": "picture", "id": "s1", "settings": {"prompt": "nine swords", "goal": "set"}},
        {"add": "model", "id": "s2", "after": "s1", "settings": {"engine": "sf3d"}},
        {"add": "split", "id": "s3", "after": "s2"}, {"add": "rig", "id": "s4", "after": "s3"}])
    canvas_id = made["canvas"]["id"]
    assert made["plan"] == ["Run 1: Picture → 3D model → Split into props", "Run 2, once for each prop from run 1: Rig"]
    assert ms.list_canvases(studio)["canvases"][0]["name"] == "Swords"
    started = ms.run_canvas(studio, canvas_id=canvas_id)
    status = ms.canvas_status(studio, run_id=started["run_id"], wait_seconds=0)
    for _ in range(100):
        if status["status"] != "running":
            break
        status = ms.canvas_status(studio, run_id=started["run_id"])
    assert status["status"] == "done" and len(status["results"]["s4"]) == 2
    assert studio.jobs.started == ["picture", "model", "split", "rigged", "rigged"]
    boxes = ms.get_canvas(studio, canvas_id=canvas_id)["boxes"]
    assert boxes["s3"]["status"] == "done" and len(boxes["s3"]["files"]) == 2
    assert ms.check_canvas(studio, canvas_id=canvas_id, box_id="s4", mode="only")["plan"] == ["Run 1, once for each of 2 props: Rig"]


def test_an_agent_is_told_why_a_canvas_cannot_run_and_a_stale_save_is_refused(studio):
    made = ms.edit_canvas(studio, ops=[{"add": "picture", "id": "p"}])
    assert made["cannot_run_yet"] == "Picture: describe it, or pick a picture already made."
    old = ms.get_canvas(studio, canvas_id=made["canvas"]["id"])["canvas"]
    ms.edit_canvas(studio, canvas_id=old["id"], ops=[{"rename": "the user's name"}])
    with pytest.raises(ms.StudioError, match="changed somewhere else"):
        ms.save_canvas(studio, canvas={**old, "name": "an agent's old copy"})
    with pytest.raises(ms.StudioError, match="needs box_id"):
        ms.run_canvas(studio, canvas_id=old["id"], mode="only")
    assert ms.canvas_status(studio) == {"status": "idle", "note": "No canvas is running."}
