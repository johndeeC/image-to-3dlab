"""Footsteps on a move, from the studio's side (viewer/footsteps_api.py and its two routes in
viewer/generate_api.py), over real HTTP against the real handler, on a small walking skeleton."""

from __future__ import annotations

import functools
import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "viewer"), str(REPO / "tests")]

import footsteps_api
import generate_api
import sound_api
from glb_fixtures import walking_glb

from image_to_3dlab import footsteps, sound_post

RUN = "knight__rig__20261120-101010"
MOVE = f"animate/{RUN}/knight_walk.glb"
SOUND = "boots__20261120-091306"
SET = "boots_set__20261120-093300"


@pytest.fixture
def output(tmp_path):
    out = tmp_path / "output"
    run = out / "animate" / RUN
    run.mkdir(parents=True)
    (run / "knight_rigged.glb").write_bytes(walking_glb())
    (run / "knight_walk.glb").write_bytes(walking_glb(seconds=1.2))
    (run / "knight_walk.provenance.json").write_text(json.dumps(
        {"motion": {"preset": "walk", "label": "Walk", "speed": 1.25, "loop": True, "in_place": True}}))
    tone = (0.4 * np.sin(np.arange(4800) * 0.2)).astype(np.float32)
    kept = out / "sounds" / SOUND
    kept.mkdir(parents=True)
    sound_post.write_wav(kept / "sound.wav", tone)
    (kept / "sound.provenance.json").write_text(json.dumps({"license": {"name": "Apache-2.0"}}))
    takes = out / "sounds" / SET
    takes.mkdir()
    for n in (1, 2, 3):
        sound_post.write_wav(takes / f"boots_0{n}.wav", tone)
    (takes / "set.provenance.json").write_text(json.dumps(
        {"license": {"name": "Apache-2.0", "classification": "commercial-clear"}, "variation": {"seed": 4}}))
    return out


def test_the_steps_are_found_and_nothing_is_written(output):
    found = footsteps_api.find(output, MOVE)
    assert (found["move"], found["label"]) == ("walk", "Walk") and found["speed"] == 1.25 and found["loop"] is True
    assert found["seconds"] == pytest.approx(1.2)
    assert [(cue["foot"], cue["time_s"]) for cue in found["cues"]] == [
        ("left", pytest.approx(0.3, abs=0.04)), ("right", pytest.approx(0.9, abs=0.04))]
    assert found["words"] == "2 steps in each 1.2 s loop: left, right."
    assert found["saved"] is None
    assert not list(output.rglob("*.cues.json"))


def test_saving_writes_the_marks_beside_the_move_and_points_at_the_set(output):
    saved = footsteps_api.save(output, {"file": MOVE, "sound_id": SET})
    path = output / "animate" / RUN / "knight_walk.cues.json"
    assert saved["cues_file"] == f"animate/{RUN}/knight_walk.cues.json" and path.is_file()
    record = json.loads(path.read_text())
    assert (record["move"], record["model"], record["speed"], record["loop_seconds"]) == ("walk", "knight_walk.glb", 1.25, 1.2)
    files = record["sound"]["files"]
    assert files == [f"../../sounds/{SET}/boots_0{n}.wav" for n in (1, 2, 3)]
    assert all((path.parent / file).resolve().is_file() for file in files)      # found from the cue file's own folder
    assert record["cues"][0]["sound"] == {"id": SET, "files": files}
    # no audio of its own, so no licence of its own: it points at the sound's record
    assert record["sound"]["license"] == {"name": "Apache-2.0", "classification": "commercial-clear"}
    assert (path.parent / record["sound"]["record"]).resolve() == (output / "sounds" / SET / "set.provenance.json").resolve()
    assert "never the same one twice" in record["sound"]["play"]
    assert footsteps_api.find(output, MOVE)["saved"] == {"on": True, "sound_id": SET, "file": saved["cues_file"]}


def test_a_single_sound_no_sound_and_switched_off(output):
    one = footsteps_api.save(output, {"file": MOVE, "sound_id": SOUND})
    assert one["sound"]["files"] == [f"../../sounds/{SOUND}/sound.wav"] and one["sound"]["kind"] == "sound"
    bare = footsteps_api.save(output, {"file": MOVE, "on": False})
    assert bare["sound"] is None and bare["on"] is False and len(bare["cues"]) == 2
    assert footsteps.read_cues(output / MOVE)["on"] is False
    with pytest.raises(ValueError):
        footsteps_api.save(output, {"file": MOVE, "on": "yes"})


@pytest.mark.parametrize("file", [
    None, "", 7, "animate/../../etc/passwd", f"animate/{RUN}/../{RUN}/knight_walk.glb", f"/animate/{RUN}/knight_walk.glb",
    f"animate/{RUN}/knight_rigged.glb", f"animate/{RUN}/missing.glb", f"animate/{RUN}/knight_walk.provenance.json",
    f"sounds/{SOUND}/sound.wav", f"animate/{RUN}/sub/knight_walk.glb", f"animate/{RUN}/..glb"])
def test_only_a_move_beside_a_rigged_model_can_be_named(output, file):
    with pytest.raises(LookupError):
        footsteps_api.find(output, file)
    with pytest.raises(LookupError):
        footsteps_api.save(output, {"file": file, "sound_id": SET})
    assert not list(output.rglob("*.cues.json"))


def test_a_link_out_of_the_folder_is_not_a_move(output, tmp_path):
    outside = tmp_path / "elsewhere.glb"
    outside.write_bytes(walking_glb())
    (output / "animate" / RUN / "knight_run.glb").symlink_to(outside)
    with pytest.raises(LookupError):
        footsteps_api.find(output, f"animate/{RUN}/knight_run.glb")


@pytest.mark.parametrize("sound_id", ["nope__20261120-000000", "../" + SOUND, ".tries", 5, SOUND + "/sound.wav"])
def test_an_unknown_sound_is_refused_and_nothing_is_written(output, sound_id):
    with pytest.raises(LookupError, match="no such kept sound"):
        footsteps_api.save(output, {"file": MOVE, "sound_id": sound_id})
    assert not list(output.rglob("*.cues.json"))


def test_a_file_that_is_not_a_model_says_so(output):
    (output / "animate" / RUN / "knight_broken.glb").write_bytes(b"not a model")
    with pytest.raises(LookupError):
        footsteps_api.find(output, f"animate/{RUN}/knight_broken.glb")


def test_the_moves_name_comes_from_the_file_when_there_is_no_record(output):
    run = output / "animate" / RUN
    (run / "knight_run_b.glb").write_bytes(walking_glb(seconds=0.5))
    found = footsteps_api.find(output, f"animate/{RUN}/knight_run_b.glb")
    assert found["move"] == "run_b" and found["speed"] is None and found["loop"] is True   # it ends where it began


# ----------------------------------------------------------------------------- over HTTP

@pytest.fixture
def studio(output, tmp_path, monkeypatch):
    monkeypatch.setattr(sound_api, "MANAGER", sound_api.SoundJobManager(output / "sounds"))
    monkeypatch.setattr(generate_api, "OUTPUT_ROOT", output)
    monkeypatch.setattr(generate_api.usage_api, "note_request", lambda *a, **k: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(generate_api.Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def call(httpd, method, path, body=None, headers=None):
    sent = {"Origin": f"http://127.0.0.1:{httpd.server_port}", "X-AssetFurnace-Request": "studio",
            "Content-Type": "application/json", **(headers or {})}
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
    try:
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=sent)
        response = conn.getresponse()
        raw = response.read()
        return response.status, (json.loads(raw) if "json" in (response.getheader("Content-Type") or "") and raw else raw)
    finally:
        conn.close()


def test_find_then_save_over_http(studio, output):
    status, found = call(studio, "GET", f"/api/footsteps?file={MOVE}")
    assert status == 200 and len(found["cues"]) == 2 and found["saved"] is None and found["base"] == "/output/"
    assert [foot["foot"] for foot in found["feet"]] == ["left", "right"]
    status, saved = call(studio, "POST", "/api/footsteps", {"file": MOVE, "sound_id": SET})
    assert status == 200 and (output / saved["cues_file"]).is_file()
    # the saved marks are served like any other output file
    status, served = call(studio, "GET", "/output/" + saved["cues_file"])
    assert status == 200 and served["kind"] == "move_cues"


def test_bad_requests_are_refused_in_words(studio, output):
    assert call(studio, "GET", "/api/footsteps")[0] == 404
    assert call(studio, "GET", "/api/footsteps?file=animate/../../secret.glb")[0] == 404
    status, told = call(studio, "POST", "/api/footsteps", {"file": MOVE, "sound_id": "nope__20261120-000000"})
    assert status == 404 and told["error"] == "there is no such kept sound"
    assert call(studio, "POST", "/api/footsteps", {"file": MOVE, "on": "yes"})[0] == 422
    assert call(studio, "POST", "/api/footsteps", {"file": f"animate/{RUN}/knight_rigged.glb"})[0] == 404
    assert not list(output.rglob("*.cues.json"))


def test_saving_is_behind_the_request_guard(studio, output):
    """Another site's page cannot save marks: no studio header, or a foreign Origin, is refused."""
    assert call(studio, "POST", "/api/footsteps", {"file": MOVE}, headers={"X-AssetFurnace-Request": ""})[0] == 403
    assert call(studio, "POST", "/api/footsteps", {"file": MOVE}, headers={"Origin": "https://evil.example"})[0] == 403
    assert not list(output.rglob("*.cues.json"))


def test_with_a_studio_key_set_the_routes_need_it(studio, output, monkeypatch):
    monkeypatch.setattr(generate_api.Handler, "studio_token", "s3cret-key")
    assert call(studio, "GET", f"/api/footsteps?file={MOVE}")[0] == 401
    assert call(studio, "POST", "/api/footsteps", {"file": MOVE})[0] == 401
    assert not list(output.rglob("*.cues.json"))
