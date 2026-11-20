"""The studio's sound endpoints (viewer/generate_api.py), over real HTTP against the real handler.

The engine is never started: a job's work is replaced by one that lands a try made with the
shipped clean-up code. What is checked is the door: the request guard, refusals, that a
missing engine points at Setup and downloads nothing, and that tries play and can be kept.
"""

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
sys.path[:0] = [str(REPO), str(REPO / "viewer")]

import generate_api
import sound_api

from image_to_3dlab import sound_post

ASK = {"prompt": "a heavy wooden door creaks open", "seconds": 2, "seed": 5, "count": 2}


def land_tries(job, manager):
    """What the engine's runner does, without the engine: write each try and report it."""
    job.set_status("running")
    for number in range(1, job.request["count"] + 1):
        tone = (0.5 * np.sin(np.arange(4800) * 0.05 * number)).astype(np.float32)
        wav = sound_post.write_wav(job.try_file(number), tone)
        facts = sound_post.inspect_wav(wav)
        post = {"trim": True, "fade_ms": 5.0, "peak": 0.98}
        sound_api.write_sidecar(wav, sound_api.sidecar(engine=job.engine, request=job.request, number=number,
                                                       wav=wav, facts=facts, post=post))
        job.tries.append({"number": number, "seed": sound_api.try_seed(job.request["seed"], number),
                          "seconds": facts["seconds"], "peak": facts["peak"], "bars": [1.0],
                          "url": f"/api/sound/{job.id}/try-{number}.wav", "kept": None})
    job.set_status("done")
    manager.finish(job)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    output = tmp_path / "output"
    manager = sound_api.SoundJobManager(output / "sounds")
    monkeypatch.setattr(sound_api, "MANAGER", manager)
    monkeypatch.setattr(sound_api, "run_job", land_tries)
    monkeypatch.setattr(sound_api, "missing", lambda engine=sound_api.MOSS, hub=None: [])
    monkeypatch.setattr(generate_api, "OUTPUT_ROOT", output)
    monkeypatch.setattr(generate_api.usage_api, "note_request", lambda *a, **k: None)
    monkeypatch.setattr(generate_api, "busy_reason", lambda starting: None)
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
        kind = response.getheader("Content-Type") or ""
        return response.status, (json.loads(raw) if "json" in kind and raw else raw), kind
    finally:
        conn.close()


def wait_done(httpd, job_id):
    for _ in range(200):
        _, told, _ = call(httpd, "GET", f"/api/sound/{job_id}/status")
        if told["status"] in {"done", "error", "cancelled"}:
            return told
        threading.Event().wait(0.02)
    raise AssertionError("the job never ended")


def test_make_listen_keep(studio, tmp_path):
    status, started, _ = call(studio, "POST", "/api/sound", ASK)
    assert status == 202 and started["seed"] == 5 and started["count"] == 2
    assert started["status_url"] == f"/api/sound/{started['job_id']}/status"
    told = wait_done(studio, started["job_id"])
    assert told["status"] == "done" and [t["seed"] for t in told["tries"]] == [5, 6]

    status, wav, kind = call(studio, "GET", told["tries"][1]["url"])
    assert status == 200 and kind == "audio/wav" and wav[:4] == b"RIFF"
    assert call(studio, "GET", f"/api/sound/{started['job_id']}/try-3.wav")[0] == 404    # never made

    assert call(studio, "GET", "/api/sound/kept")[1] == {"base": "/output/", "sounds": []}
    status, kept, _ = call(studio, "POST", f"/api/sound/{started['job_id']}/keep", {"try": 2})
    assert status == 200 and kept["file"].startswith("sounds/a-heavy-wooden-door-creaks-open__")
    assert (tmp_path / "output" / kept["file"]).is_file()
    (listed,) = call(studio, "GET", "/api/sound/kept")[1]["sounds"]
    assert listed["file"] == kept["file"] and listed["licence"] == "Apache-2.0"
    # the kept file is served like any other output file, so the Library can play it
    status, body, _ = call(studio, "GET", "/output/" + kept["file"])
    assert status == 200 and body[:4] == b"RIFF"
    assert call(studio, "POST", f"/api/sound/{started['job_id']}/keep", {"try": 7})[0] == 422


def test_tries_are_not_served_as_plain_files(studio):
    _, started, _ = call(studio, "POST", "/api/sound", ASK)
    wait_done(studio, started["job_id"])
    assert call(studio, "GET", f"/output/sounds/.tries/{started['job_id']}/try-1/sound.wav")[0] == 404


@pytest.mark.parametrize("body,why", [
    ({**ASK, "seconds": 45}, "0.5 to 30 seconds"),
    ({**ASK, "prompt": ""}, "describe the sound"),
    ({**ASK, "kind": "voice"}, "Voice has its own page"),
    ({**ASK, "engine": "tango"}, "no sound engine"),
    ({**ASK, "count": 9}, "between 1 and 4"),
])
def test_a_request_past_the_limits_is_refused_and_nothing_starts(studio, body, why):
    status, told, _ = call(studio, "POST", "/api/sound", body)
    assert status == 422 and why in told["error"]
    assert sound_api.MANAGER.jobs == {}


def test_with_the_engine_missing_it_points_at_setup_and_downloads_nothing(studio, monkeypatch):
    monkeypatch.setattr(sound_api, "missing", lambda engine=sound_api.MOSS, hub=None: ["its model files (6.4 GB)"])
    monkeypatch.setattr(generate_api.subprocess, "Popen", lambda *a, **k: pytest.fail("started something"))
    status, told, _ = call(studio, "POST", "/api/sound", ASK)
    assert status == 503 and told["needs_setup"] is True and told["setup_id"] == "sound-effects"
    assert "Open Setup" in told["error"] and told["missing"] == ["its model files (6.4 GB)"]
    assert sound_api.MANAGER.jobs == {} and generate_api.SETUP_ACTIVE is None and generate_api.DOWNLOADS == {}
    _, defaults, _ = call(studio, "GET", "/api/sound/defaults")
    assert defaults["installed"] is False and defaults["limits"]["max_seconds"] == 30.0


def test_it_waits_while_another_heavy_job_runs(studio, monkeypatch):
    monkeypatch.setattr(generate_api, "busy_reason", lambda starting: "a picture is being made; wait for it to finish"
                        if starting == "sound" else None)
    status, told, _ = call(studio, "POST", "/api/sound", ASK)
    assert status == 409 and "a picture is being made" in told["error"]


def test_a_sound_being_made_holds_back_the_other_heavy_jobs(monkeypatch, tmp_path):
    manager = sound_api.SoundJobManager(tmp_path)
    monkeypatch.setattr(sound_api, "MANAGER", manager)
    assert generate_api.HEAVY_JOBS["sound"]() is False
    job = manager.create(sound_api.clean_request(ASK))
    assert generate_api.HEAVY_JOBS["sound"]() is True
    quiet = dict.fromkeys(generate_api.HEAVY_JOBS, lambda: False)
    monkeypatch.setattr(generate_api, "HEAVY_JOBS", {**quiet, "sound": generate_api.HEAVY_JOBS["sound"]})
    assert generate_api.busy_reason("image") == "a sound is being made; wait for it to finish"
    assert generate_api.busy_reason("sound") is None
    manager.finish(job)


def test_cancel_and_unknown_jobs(studio):
    assert call(studio, "POST", "/api/sound/" + "0" * 32 + "/cancel", {})[0] == 404
    assert call(studio, "GET", "/api/sound/" + "0" * 32 + "/status")[0] == 404
    assert call(studio, "GET", "/api/sound/..%2F..%2Fetc/status")[0] == 404
    _, started, _ = call(studio, "POST", "/api/sound", ASK)
    wait_done(studio, started["job_id"])
    status, told, _ = call(studio, "POST", f"/api/sound/{started['job_id']}/cancel", {})
    assert status == 409 and "not being made" in told["error"]          # it already finished


def test_a_page_on_another_site_cannot_start_a_sound(studio):
    status, _, _ = call(studio, "POST", "/api/sound", ASK, {"Origin": "https://evil.example"})
    assert status == 403 and sound_api.MANAGER.jobs == {}


def test_a_keyed_studio_wants_its_key_for_sounds_too(studio, monkeypatch):
    monkeypatch.setattr(generate_api.Handler, "studio_token", "s3cret-key-for-tests")
    assert call(studio, "POST", "/api/sound", ASK)[0] == 401
    assert call(studio, "GET", "/api/sound/defaults")[0] == 401
    assert call(studio, "POST", "/api/sound", ASK, {"Authorization": "Bearer s3cret-key-for-tests"})[0] == 202


def test_the_library_payload_lists_kept_sounds_beside_the_assets(tmp_path):
    import assets_api
    folder = tmp_path / "sounds" / "coin__20261120-101500"
    sound_post.write_wav(folder / "sound.wav", np.full(480, 0.5, np.float32))
    payload = assets_api.assets_payload(tmp_path)
    assert payload["assets"] == []                    # a sound is not a 3D run or a picture
    assert [s["file"] for s in payload["sounds"]] == ["sounds/coin__20261120-101500/sound.wav"]


# --------------------------------------------------------------------------- variation sets

KEPT = "boots-on-gravel__20261120-091306"


@pytest.fixture
def kept(studio, tmp_path):
    """A kept sound with its record, as Keep leaves one."""
    t = np.arange(24_000) / 48_000
    thump = (0.6 * np.sin(2 * np.pi * 180 * t) * np.exp(-9 * t)).astype(np.float32)
    wav = sound_post.write_wav(tmp_path / "output" / "sounds" / KEPT / "sound.wav", sound_post.fade(thump))
    request = sound_api.clean_request({"prompt": "heavy boots on gravel", "seed": 5, "count": 1})
    sound_api.write_sidecar(wav, sound_api.sidecar(
        engine=sound_api.MOSS, request=request, number=1, wav=wav, facts=sound_post.inspect_wav(wav),
        post={"trim": True, "fade_ms": 5.0, "peak": 0.98}))
    return studio


def test_variations_are_heard_then_saved_as_a_set(kept, tmp_path, monkeypatch):
    monkeypatch.setattr(sound_api, "missing", lambda engine=sound_api.MOSS, hub=None: ["its model files (6.4 GB)"])
    monkeypatch.setattr(generate_api.subprocess, "Popen", lambda *a, **k: pytest.fail("started something"))
    ask = {"count": 4, "how_different": "some", "change": ["pitch", "volume"]}
    status, heard, _ = call(kept, "POST", f"/api/sounds/{KEPT}/variations", ask)       # no engine needed
    assert status == 200 and heard["count"] == 4 and heard["base"] == "/output/"
    assert [v["file"] for v in heard["variations"]] == [f"boots-on-gravel_0{n}.wav" for n in (1, 2, 3, 4)]
    assert not (tmp_path / "output" / "sounds" / ".tries").exists()
    played = []
    for take in heard["variations"]:
        status, wav, kind = call(kept, "GET", take["url"])
        assert status == 200 and kind == "audio/wav" and wav[:4] == b"RIFF"
        played.append(wav)
    assert len(set(played)) == 4

    status, saved, _ = call(kept, "POST", f"/api/sounds/{KEPT}/set", {**ask, "seed": heard["seed"]})
    assert status == 200 and saved["folder"].startswith("sounds/boots-on-gravel_set__")
    folder = tmp_path / "output" / saved["folder"]
    assert [(tmp_path / "output" / f["file"]).read_bytes() for f in saved["files"]] == played   # what was heard
    record = json.loads((folder / "set.provenance.json").read_text())
    assert record["kind"] == "sound_set" and record["license"]["name"] == "Apache-2.0"
    assert record["variation"]["seed"] == heard["seed"] and record["variation"]["of"] == KEPT
    assert [f["variation"]["index"] for f in record["files"]] == [1, 2, 3, 4]
    assert all(len(f["sha256"]) == 64 for f in record["files"])
    # the Library lists it as a set, and its files are served like any kept sound
    kinds = {s["kind"]: s for s in call(kept, "GET", "/api/sound/kept")[1]["sounds"]}
    assert kinds["sound_set"]["count"] == 4 and kinds["sound"]["id"] == KEPT
    status, body, _ = call(kept, "GET", "/output/" + kinds["sound_set"]["files"][3])
    assert status == 200 and body == played[3]


@pytest.mark.parametrize("body,why", [
    ({"count": 1}, "between 2 and 12"), ({"count": 13}, "between 2 and 12"), ({"count": 99}, "between 2 and 12"),
    ({"how_different": "extreme"}, "how different"), ({"change": []}, "pick what changes"),
    ({"change": ["echo"]}, "pick what changes"),
])
def test_variations_past_a_limit_are_refused_and_nothing_is_saved(kept, tmp_path, body, why):
    for action in ("variations", "set"):
        status, told, _ = call(kept, "POST", f"/api/sounds/{KEPT}/{action}", body)
        assert status == 422 and why in told["error"]
    assert [p.name for p in (tmp_path / "output" / "sounds").iterdir()] == [KEPT]


@pytest.mark.parametrize("sound_id", ["..%2F..%2Fetc", "%2E%2E", "nobody__20261120-091306", "boots", ".tries",
                                      f"..%2Fsounds%2F{KEPT}", f"{KEPT}%0A", "a%2Fb__20261120-091306"])
def test_an_id_that_is_not_a_kept_sound_is_not_found(kept, tmp_path, sound_id):
    for action in ("variations", "set"):
        assert call(kept, "POST", f"/api/sounds/{sound_id}/{action}", {"count": 2})[0] == 404
    assert call(kept, "GET", f"/api/sounds/{sound_id}/variations/1.wav?count=2&seed=1")[0] == 404
    assert [p.name for p in (tmp_path / "output" / "sounds").iterdir()] == [KEPT]
    assert [p.name for p in tmp_path.iterdir()] == ["output"]


@pytest.mark.parametrize("tail", ["3.wav?count=2&seed=1", "0.wav?count=2&seed=1", "1.wav?count=2", "1.wav?count=99&seed=1",
                                  "1.mp3?count=2&seed=1", "..%2Fsound.wav?count=2&seed=1"])
def test_a_take_that_does_not_exist_is_not_found(kept, tail):
    assert call(kept, "GET", f"/api/sounds/{KEPT}/variations/1.wav?count=2&seed=1")[0] == 200
    assert call(kept, "GET", f"/api/sounds/{KEPT}/variations/{tail}")[0] == 404


def test_variations_sit_behind_the_same_door_as_the_rest(kept, tmp_path, monkeypatch):
    for action in ("variations", "set"):
        status, _, _ = call(kept, "POST", f"/api/sounds/{KEPT}/{action}", {"count": 2}, {"Origin": "https://evil.example"})
        assert status == 403
    monkeypatch.setattr(generate_api.Handler, "studio_token", "s3cret-key-for-tests")
    assert call(kept, "POST", f"/api/sounds/{KEPT}/set", {"count": 2})[0] == 401
    assert call(kept, "GET", f"/api/sounds/{KEPT}/variations/1.wav?count=2&seed=1")[0] == 401
    assert [p.name for p in (tmp_path / "output" / "sounds").iterdir()] == [KEPT]
    assert call(kept, "POST", f"/api/sounds/{KEPT}/set", {"count": 2},
                {"Authorization": "Bearer s3cret-key-for-tests"})[0] == 200


def test_the_library_payload_lists_sets_too(tmp_path):
    import assets_api
    folder = tmp_path / "sounds" / "coin_set__20261120-101500"
    for n in (1, 2):
        sound_post.write_wav(folder / f"coin_0{n}.wav", np.full(480, 0.5, np.float32))
    (made,) = assets_api.assets_payload(tmp_path)["sounds"]
    assert made["kind"] == "sound_set" and made["name"] == "Coin" and made["count"] == 2
    assert made["files"] == ["sounds/coin_set__20261120-101500/coin_01.wav", "sounds/coin_set__20261120-101500/coin_02.wav"]
