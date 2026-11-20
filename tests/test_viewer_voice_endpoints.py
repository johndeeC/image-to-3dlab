"""The studio's voice endpoints (viewer/generate_api.py), over real HTTP against the real handler.

No engine is started: a job's work is replaced by one that lands each try through the shipped
levelling and record code. What is checked is the door: the request guard, refusals (limits,
and any attempt to hand over a recording to copy), that a missing engine points at Setup and
downloads nothing, and that lines play, keep and join through the sound job routes.
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
import voice_api

from image_to_3dlab import sound_post

SHOP = "Welcome, traveller! Take a look at my wares. The potions are fresh today."
ASK = {"text": SHOP, "engine": "qwen", "voice": "ryan", "direction": "shout", "seed": 5, "count": 2}


def land_tries(job, manager):
    """What the engines' runner does, without an engine: say each try and report it."""
    job.set_status("running")
    for number in range(1, job.request["count"] + 1):
        line, take = voice_api.line_of(number, job.request["takes"])
        t = np.arange(24_000) / 24_000
        raw = (0.1 * np.sin(2 * np.pi * (150 + 10 * number) * t)).astype(np.float32)
        sound, post = sound_post.clean_voice(raw, 24_000, (job.request["direction"] or {}).get("id"))
        wav = sound_post.write_wav(job.try_file(number), sound)
        facts = sound_post.inspect_wav(wav)
        sound_api.write_sidecar(wav, voice_api.sidecar(engine=job.engine, request=job.request, number=number,
                                                       wav=wav, facts=facts, post=post))
        job.tries.append({"number": number, "line": line, "take": take, "seed": job.request["seed"] + number - 1,
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
    monkeypatch.setattr(voice_api, "missing", lambda engine=voice_api.KOKORO, hub=None: [])
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


def test_the_sheet_is_told_the_engines_and_voices(studio):
    status, told, _ = call(studio, "GET", "/api/voice/defaults")
    assert status == 200 and [e["label"] for e in told["engines"]] == ["Clear and quick", "With feeling"]
    assert told["engines"][1]["voices"][0]["id"] == "ryan" and len(told["directions"]) == 6
    _, kinds, _ = call(studio, "GET", "/api/sound/defaults")
    assert [(k["id"], k["ready"], k.get("sheet")) for k in kinds["kinds"]][-1] == ("voice", True, "voice")


def test_say_listen_keep_and_join(studio, tmp_path):
    status, started, _ = call(studio, "POST", "/api/voice", ASK)
    assert status == 202 and started["count"] == 6 and started["takes"] == 2 and started["seed"] == 5
    assert started["lines"] == ["Welcome, traveller!", "Take a look at my wares.", "The potions are fresh today."]
    assert started["status_url"] == f"/api/sound/{started['job_id']}/status"      # a voice job is a sound job
    told = wait_done(studio, started["job_id"])
    assert told["status"] == "done" and told["kind"] == "voice" and told["voice"]["voice_label"] == "Ryan"
    assert [(t["line"], t["take"]) for t in told["tries"]] == [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2)]

    status, wav, kind = call(studio, "GET", told["tries"][5]["url"])
    assert status == 200 and kind == "audio/wav" and wav[:4] == b"RIFF"

    status, kept, _ = call(studio, "POST", f"/api/sound/{started['job_id']}/keep", {"try": 4})
    assert status == 200 and kept["file"].startswith("sounds/Welcome-traveller-Take-a-look")
    (listed,) = call(studio, "GET", "/api/sound/kept")[1]["sounds"]
    assert listed["sound_kind"] == "voice" and listed["prompt"] == "Take a look at my wares."

    parts = [{"job_id": started["job_id"], "try": n} for n in (1, 4, 5)]
    status, joined, _ = call(studio, "POST", "/api/voice/join", {"parts": parts, "name": "Shop hello"})
    assert status == 200 and joined["lines"] == 3 and joined["base"] == "/output/"
    record = json.loads((tmp_path / "output" / joined["file"]).with_name("sound.provenance.json").read_text())
    assert record["voice"]["text"] == SHOP and record["voice"]["cloned"] is False
    assert record["license"]["name"] == "Apache-2.0"
    status, body, _ = call(studio, "GET", "/output/" + joined["file"])     # served like any kept sound
    assert status == 200 and body[:4] == b"RIFF"
    assert len(call(studio, "GET", "/api/sound/kept")[1]["sounds"]) == 2


def test_ten_or_more_tries_can_still_be_played(studio):
    _, started, _ = call(studio, "POST", "/api/voice", {**ASK, "count": 4})
    told = wait_done(studio, started["job_id"])
    assert len(told["tries"]) == 12
    assert call(studio, "GET", f"/api/sound/{started['job_id']}/try-12.wav")[0] == 200
    assert call(studio, "GET", f"/api/sound/{started['job_id']}/try-13.wav")[0] == 404


def test_a_line_made_again_leaves_the_others_on_the_page(studio):
    _, first, _ = call(studio, "POST", "/api/voice", ASK)
    wait_done(studio, first["job_id"])
    _, again, _ = call(studio, "POST", "/api/voice", {**ASK, "text": None, "lines": ["Take a look at my wares."],
                                                    "seed": None, "using": [first["job_id"]]})
    wait_done(studio, again["job_id"])
    assert again["lines"] == ["Take a look at my wares."] and again["count"] == 2
    assert call(studio, "GET", f"/api/sound/{first['job_id']}/try-1.wav")[0] == 200
    parts = [{"job_id": first["job_id"], "try": 1}, {"job_id": again["job_id"], "try": 2}]
    assert call(studio, "POST", "/api/voice/join", {"parts": parts})[0] == 200


@pytest.mark.parametrize("body,why", [
    ({**ASK, "text": ""}, "type the line to say first"),
    ({**ASK, "engine": "bark"}, "no voice engine"),
    ({**ASK, "voice": "af_heart"}, "With feeling has no voice"),
    ({**ASK, "count": 9}, "between 1 and 4 tries"),
    ({**ASK, "engine": "kokoro", "voice": None, "count": 1}, "cannot be told how to say a line"),
    ({**ASK, "ref_audio": "me.wav"}, "does not copy a person's voice"),
    ({**ASK, "voice": "../../me.wav"}, "does not copy a person's voice"),
])
def test_a_request_past_the_limits_is_refused_and_nothing_starts(studio, body, why):
    status, told, _ = call(studio, "POST", "/api/voice", body)
    assert status == 422 and why in told["error"]
    assert sound_api.MANAGER.jobs == {}


def test_there_is_no_door_for_a_recording(studio):
    """No upload, no record, no clone route: the consent rule is the absence of the door."""
    for path in ("/api/voice/upload", "/api/voice/clone", "/api/voice/record", "/api/voice/reference"):
        assert call(studio, "POST", path, {"x": 1})[0] in {404, 405}


def test_with_the_engine_missing_it_points_at_setup_and_downloads_nothing(studio, monkeypatch):
    monkeypatch.setattr(voice_api, "missing", lambda engine=voice_api.KOKORO, hub=None: ["its model files (3.08 GB)"])
    monkeypatch.setattr(generate_api.subprocess, "Popen", lambda *a, **k: pytest.fail("started something"))
    status, told, _ = call(studio, "POST", "/api/voice", ASK)
    assert status == 503 and told["needs_setup"] is True and told["setup_id"] == "voice-qwen"
    assert "Open Setup" in told["error"] and told["missing"] == ["its model files (3.08 GB)"]
    assert sound_api.MANAGER.jobs == {} and generate_api.SETUP_ACTIVE is None and generate_api.DOWNLOADS == {}
    _, defaults, _ = call(studio, "GET", "/api/voice/defaults")
    assert [e["installed"] for e in defaults["engines"]] == [False, False]


def test_it_waits_while_another_heavy_job_runs(studio, monkeypatch):
    monkeypatch.setattr(generate_api, "busy_reason", lambda starting: "a picture is being made; wait for it to finish"
                        if starting == "sound" else None)
    status, told, _ = call(studio, "POST", "/api/voice", ASK)
    assert status == 409 and "a picture is being made" in told["error"]


@pytest.mark.parametrize("body,status", [
    ({}, 422), ({"parts": [{"job_id": "0" * 32, "try": 1}]}, 404), ({"parts": [{"job_id": "x", "try": "1"}]}, 422),
])
def test_a_bad_join_is_refused(studio, body, status):
    assert call(studio, "POST", "/api/voice/join", body)[0] == status
    assert call(studio, "GET", "/api/sound/kept")[1]["sounds"] == []


def test_a_page_on_another_site_cannot_start_a_voice_line(studio):
    status, _, _ = call(studio, "POST", "/api/voice", ASK, {"Origin": "https://evil.example"})
    assert status == 403 and sound_api.MANAGER.jobs == {}
    assert call(studio, "POST", "/api/voice/join", {"parts": []}, {"Origin": "https://evil.example"})[0] == 403


def test_a_keyed_studio_wants_its_key_for_voice_too(studio, monkeypatch):
    monkeypatch.setattr(generate_api.Handler, "studio_token", "s3cret-key-for-tests")
    assert call(studio, "POST", "/api/voice", ASK)[0] == 401
    assert call(studio, "GET", "/api/voice/defaults")[0] == 401
    assert call(studio, "POST", "/api/voice", ASK, {"Authorization": "Bearer s3cret-key-for-tests"})[0] == 202
