"""The studio's music endpoints (viewer/generate_api.py), over real HTTP against the real handler.

No engine is started: a job's work is replaced by one that lands each try through the shipped
levelling and record code. What is checked is the door: the request guard, refusals (limits,
and any attempt to hand over a recording), that a missing engine points at Setup and
downloads nothing, and that tunes play and keep through the sound job routes, in stereo.
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
import music_api
import sound_api

from image_to_3dlab import sound_post

ASK = {"prompt": "calm fantasy village theme, harp, flute", "seconds": 10, "seed": 5, "count": 2, "plan": False}


def land_tries(job, manager):
    """What the engine's runner does, without an engine: make each try and report it."""
    job.set_status("running")
    for number in range(1, job.request["count"] + 1):
        seconds = job.request["seconds"] + (1.3 if job.request["loop"] else 0)
        t = np.arange(int(seconds * 48_000)) / 48_000
        raw = np.stack([0.2 * np.sin(2 * np.pi * (220 + number) * t), 0.2 * np.sin(2 * np.pi * 330 * t)], axis=1)
        if job.request["loop"]:
            sound, post = sound_post.clean_music_loop(raw, 48_000, job.request["seconds"])
            points = (post["loop"]["start_sample"], post["loop"]["end_sample"])
        else:
            (sound, post), points = sound_post.clean_music(raw, 48_000), None
        wav = sound_post.write_wav(job.try_file(number), sound, loop=points)
        facts = sound_post.inspect_wav(wav, want_channels=2)
        sound_api.write_sidecar(wav, music_api.sidecar(engine=job.engine, request=job.request, number=number,
                                                       wav=wav, facts=facts, post=post))
        job.tries.append({"number": number, "seed": job.request["seed"] + number - 1, "seconds": facts["seconds"],
                          "peak": facts["peak"], "bars": [1.0], "level_db": post["loudness"]["measured"],
                          "url": f"/api/sound/{job.id}/try-{number}.wav", "kept": None})
    job.set_status("done")
    manager.finish(job)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    output = tmp_path / "output"
    manager = sound_api.SoundJobManager(output / "sounds")
    monkeypatch.setattr(sound_api, "MANAGER", manager)
    monkeypatch.setattr(sound_api, "run_job", land_tries)
    monkeypatch.setattr(music_api, "missing", lambda engine=music_api.ACE_STEP, hub=None: [])
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


def test_the_sheet_is_told_the_limits_and_the_switches(studio):
    status, told, _ = call(studio, "GET", "/api/music/defaults")
    assert status == 200 and told["installed"] is True and told["limits"]["tries"] == 2
    assert told["limits"]["min_seconds"] == 10 and told["limits"]["max_seconds"] == 240
    assert told["plan"]["label"] == "Plan the song first" and told["singing"]["label"] == "With singing"


def test_make_listen_and_keep_in_stereo(studio, tmp_path):
    status, started, _ = call(studio, "POST", "/api/music", ASK)
    assert status == 202 and started["count"] == 2 and started["seed"] == 5 and started["seconds"] == 10.0
    assert started["plan"] is False and started["loop"] is False and started["singing"] is False
    assert started["status_url"] == f"/api/sound/{started['job_id']}/status"      # a music job is a sound job
    told = wait_done(studio, started["job_id"])
    assert told["status"] == "done" and told["kind"] == "music" and told["music"]["plan"] is False
    assert [t["number"] for t in told["tries"]] == [1, 2]

    status, wav, kind = call(studio, "GET", told["tries"][1]["url"])
    assert status == 200 and kind == "audio/wav" and wav[:4] == b"RIFF"
    assert int.from_bytes(wav[22:24], "little") == 2                              # two channels in the file

    status, kept, _ = call(studio, "POST", f"/api/sound/{started['job_id']}/keep", {"try": 2, "name": "Village"})
    assert status == 200 and kept["file"].startswith("sounds/Village__")
    (listed,) = call(studio, "GET", "/api/sound/kept")[1]["sounds"]
    assert listed["sound_kind"] == "music" and listed["prompt"] == ASK["prompt"] and listed["licence"] == "MIT"
    assert listed["loop"] is False and listed["seconds"] == pytest.approx(10.0, abs=0.05)
    record = json.loads((tmp_path / "output" / kept["file"]).with_name("sound.provenance.json").read_text())
    assert record["music"]["instrumental"] is True and record["output"]["channels"] == 2
    status, body, _ = call(studio, "GET", "/output/" + kept["file"])              # served like any kept sound
    assert status == 200 and body[:4] == b"RIFF"
    # music has no variations
    status, told, _ = call(studio, "POST", f"/api/sounds/{listed['id']}/variations", {"count": 4})
    assert status == 422 and "music has no variations" in told["error"]


def test_a_kept_music_loop_is_listed_as_one_that_plays_round(studio):
    _, started, _ = call(studio, "POST", "/api/music", {**ASK, "loop": True, "count": 1})
    assert started["loop"] is True
    wait_done(studio, started["job_id"])
    call(studio, "POST", f"/api/sound/{started['job_id']}/keep", {"try": 1})
    (listed,) = call(studio, "GET", "/api/sound/kept")[1]["sounds"]
    assert listed["sound_kind"] == "music" and listed["loop"] is True


@pytest.mark.parametrize("body,why", [
    ({**ASK, "prompt": ""}, "describe the music first"),
    ({**ASK, "engine": "suno"}, "no music engine"),
    ({**ASK, "seconds": 600}, "from 10 to 240 seconds"),
    ({**ASK, "count": 9}, "between 1 and 4"),
    ({**ASK, "singing": True}, "write the words to sing first"),
    ({**ASK, "loop": True, "lyrics": "la la"}, "a loop has no singing"),
    ({**ASK, "reference_audio": "their_song.mp3"}, "words only"),
    ({**ASK, "src_audio": "their_song.mp3"}, "words only"),
])
def test_a_request_past_the_limits_is_refused_and_nothing_starts(studio, body, why):
    status, told, _ = call(studio, "POST", "/api/music", body)
    assert status == 422 and why in told["error"]
    assert sound_api.MANAGER.jobs == {}


def test_there_is_no_door_for_a_recording(studio):
    for path in ("/api/music/upload", "/api/music/cover", "/api/music/reference"):
        assert call(studio, "POST", path, {"x": 1})[0] in {404, 405}


def test_with_the_engine_missing_it_points_at_setup_and_downloads_nothing(studio, monkeypatch):
    monkeypatch.setattr(music_api, "missing", lambda engine=music_api.ACE_STEP, hub=None: ["its model files (10.1 GB)"])
    monkeypatch.setattr(generate_api.subprocess, "Popen", lambda *a, **k: pytest.fail("started something"))
    status, told, _ = call(studio, "POST", "/api/music", ASK)
    assert status == 503 and told["needs_setup"] is True and told["setup_id"] == "music"
    assert "Open Setup" in told["error"] and told["missing"] == ["its model files (10.1 GB)"]
    assert sound_api.MANAGER.jobs == {} and generate_api.SETUP_ACTIVE is None and generate_api.DOWNLOADS == {}
    assert call(studio, "GET", "/api/music/defaults")[1]["installed"] is False


def test_it_waits_while_another_heavy_job_runs(studio, monkeypatch):
    monkeypatch.setattr(generate_api, "busy_reason", lambda starting: "a picture is being made; wait for it to finish"
                        if starting == "sound" else None)
    status, told, _ = call(studio, "POST", "/api/music", ASK)
    assert status == 409 and "a picture is being made" in told["error"]


def test_a_page_on_another_site_cannot_start_music(studio):
    status, _, _ = call(studio, "POST", "/api/music", ASK, {"Origin": "https://evil.example"})
    assert status == 403 and sound_api.MANAGER.jobs == {}
