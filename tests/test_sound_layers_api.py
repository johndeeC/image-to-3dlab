"""Layers (viewer/sound_layers_api.py) and their endpoints, over real HTTP against the real
handler. No engine runs: a try is landed by the shipped clean-up code, as in the other sound
endpoint tests. Checked: where layers come from and what licence each carries, the record a
saved stack gets, that it then works as a kept sound, and the door (guard, limits, paths)."""

from __future__ import annotations

import functools
import http.client
import io
import json
import os
import sys
import threading
import time
import uuid
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "viewer")]

import generate_api
import sound_api
import sound_layers_api as layers

from image_to_3dlab import sound_post

ASK = {"prompt": "arrow whoosh past, fast air, dry", "seconds": 1, "seed": 5, "count": 2}


def tone(seconds=0.2, hz=440.0, level=0.5, rate=48_000):
    return (level * np.sin(2 * np.pi * hz * np.arange(int(seconds * rate)) / rate)).astype(np.float32)


def wav_file(samples, rate=48_000, channels=1) -> bytes:
    held = io.BytesIO()
    with wave.open(held, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(np.round(np.asarray(samples) * 32767).astype("<i2").tobytes())
    return held.getvalue()


def land_tries(job, manager):
    """What the engine's runner does, without the engine."""
    job.set_status("running")
    for number in range(1, job.request["count"] + 1):
        wav = sound_post.write_wav(job.try_file(number), tone(0.2, 300 * number))
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
    layers._MIXES.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(generate_api.Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def call(httpd, method, path, body=None, headers=None, raw=None):
    sent = {"Origin": f"http://127.0.0.1:{httpd.server_port}", "X-AssetFurnace-Request": "studio",
            "Content-Type": "application/json", **(headers or {})}
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
    try:
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers=sent)
        response = conn.getresponse()
        got = response.read()
        kind = response.getheader("Content-Type") or ""
        return response.status, (json.loads(got) if "json" in kind and got else got), kind
    finally:
        conn.close()


def upload(httpd, data, filename="thud.wav", headers=None, **fields):
    edge = uuid.uuid4().hex
    parts = [f'--{edge}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
             for key, value in fields.items()]
    parts.append(f'--{edge}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode() + data + b"\r\n")
    body = b"".join(parts) + f"--{edge}--\r\n".encode()
    return call(httpd, "POST", "/api/sound/layers/upload", raw=body,
                headers={"Content-Type": f"multipart/form-data; boundary={edge}", **(headers or {})})


def made_try(httpd, number=1):
    _, started, _ = call(httpd, "POST", "/api/sound", ASK)
    for _ in range(200):
        _, told, _ = call(httpd, "GET", f"/api/sound/{started['job_id']}/status")
        if told["status"] == "done":
            break
        threading.Event().wait(0.02)
    status, layer, _ = call(httpd, "POST", "/api/sound/layers/add", {"job_id": started["job_id"], "try": number})
    assert status == 200, layer
    return layer


# ------------------------------------------------------------------ licences, as stated

def test_what_you_say_about_your_file_is_what_is_recorded():
    mine = layers.stated_licence("mine", None)
    assert (mine["name"], mine["classification"], mine["stated_by"]) == ("Your own work", "commercial-clear", "you")
    cc0 = layers.stated_licence("cc0", "https://freesound.org/s/1/")
    assert (cc0["name"], cc0["classification"]) == ("CC0", "commercial-clear")
    by = layers.stated_licence("CC-BY", "https://freesound.org/s/2/")
    assert by["classification"] == "commercial-conditional" and by["attribution"] == "https://freesound.org/s/2/"
    assert any("Credit" in term for term in by["conditions"])


def test_nothing_stated_or_no_link_to_check_is_not_checked():
    for choice in (None, "", "not-checked"):
        told = layers.stated_licence(choice, "https://example.com/a")
        assert (told["name"], told["classification"], told["verified"]) == ("Not checked", "unverified", False)
    no_link = layers.stated_licence("cc0", None)
    assert no_link["classification"] == "unverified" and no_link["verified"] is False
    assert no_link["name"] == "Not checked (CC0 stated, no link given)" and no_link["stated"] == "cc0"
    with pytest.raises(ValueError, match="Not checked, Mine, CC0"):
        layers.stated_licence("gpl", None)


@pytest.mark.parametrize("link", ["freesound.org/s/1", "file:///etc/passwd", "javascript:alert(1)", "https://", "https://a/" + "x" * 500])
def test_where_its_from_is_a_web_address_or_nothing(link):
    with pytest.raises(ValueError, match="web address"):
        layers.clean_link(link)
    assert layers.clean_link("  ") is None and layers.clean_link("https://freesound.org/s/1/") == "https://freesound.org/s/1/"


def test_the_choices_the_studio_shows_start_at_not_checked():
    told = layers.options_payload()
    assert [item["label"] for item in told["licences"]] == ["Not checked", "Mine", "CC0", "CC-BY (credit needed)"]
    assert told["default_licence"] == "not-checked" and told["max"] == 8 and told["upload_mb"] == 20
    assert told["limits"]["start_ms"] == {"min": 0.0, "max": 10000.0}
    assert all(len(item["hint"]) < 110 for item in told["licences"])


# ------------------------------------------------------------------ where a layer comes from

def test_a_try_becomes_a_layer_with_the_engines_licence_and_outlives_the_next_batch(studio, tmp_path):
    layer = made_try(studio, 2)
    assert layer["from"] == "generated" and layer["licence"] == "Apache-2.0" and layer["checked"] is True
    assert layer["label"] == ASK["prompt"] and layer["seconds"] == 0.2 and len(layer["bars"]) == 48
    record = json.loads((tmp_path / "output/sounds/.layers" / layer["layer"] / "layer.json").read_text())
    assert record["source"]["seed"] == 6 and record["source"]["try"] == 2 and record["source"]["engine"] == "moss"
    call(studio, "POST", "/api/sound", ASK)                         # the next batch clears the tries...
    status, wav, kind = call(studio, "GET", layer["url"])           # ...the layer is still here
    assert status == 200 and kind == "audio/wav" and wav[:4] == b"RIFF"


def test_your_own_file_is_brought_to_one_channel_at_48k_and_hashed_as_given(studio, tmp_path):
    import hashlib
    stereo = np.stack([tone(0.25, rate=44_100), tone(0.25, rate=44_100)], axis=1)
    data = wav_file(stereo, 44_100, 2)
    status, layer, _ = upload(studio, data, "My Thud!.wav", licence="cc0", link="https://freesound.org/s/1/")
    assert status == 200, layer
    assert (layer["from"], layer["licence"], layer["checked"], layer["where"]) == (
        "file", "CC0", True, "https://freesound.org/s/1/")
    folder = tmp_path / "output/sounds/.layers" / layer["layer"]
    facts = sound_post.inspect_wav(folder / "layer.wav")           # passes the check a kept sound must
    assert (facts["sample_rate"], facts["channels"], facts["seconds"]) == (48_000, 1, 0.25)
    source = json.loads((folder / "layer.json").read_text())["source"]
    assert source["sha256"] == hashlib.sha256(data).hexdigest() and source["filename"] == "My-Thud.wav"
    assert source["was"] == {"sample_rate": 44_100, "channels": 2, "bits": 16, "seconds": 0.25,
                             "resampled": True, "mixed_down": True}


def test_a_file_with_nothing_said_about_it_is_not_checked(studio):
    status, layer, _ = upload(studio, wav_file(tone()))
    assert status == 200 and layer["licence"] == "Not checked" and layer["checked"] is False


@pytest.mark.parametrize("data, name, fields, why", [
    (b"ID3 this is an mp3", "song.mp3", {}, "bring a WAV file"),
    (b"not really a wav", "fake.wav", {}, "not a WAV this can read"),
    (wav_file(np.zeros(4800)), "quiet.wav", {}, "silent"),
    (wav_file(tone()), "a.wav", {"licence": "gpl"}, "the licence is one of"),
    (wav_file(tone()), "a.wav", {"licence": "cc0", "link": "not a link"}, "web address"),
    (wav_file(np.full(8_000 * 31, 0.1), 8_000), "long.wav", {}, "up to 30 seconds"),
])
def test_a_file_that_cannot_be_used_is_refused_and_nothing_is_staged(studio, tmp_path, data, name, fields, why):
    status, told, _ = upload(studio, data, name, **fields)
    assert status == 422 and why in told["error"]
    bench = tmp_path / "output/sounds/.layers"
    assert not bench.exists() or not list(bench.iterdir())


def test_an_upload_past_the_size_limit_is_refused_before_it_is_read(studio):
    conn = http.client.HTTPConnection("127.0.0.1", studio.server_port, timeout=10)
    try:
        conn.putrequest("POST", "/api/sound/layers/upload")
        for key, value in {"Origin": f"http://127.0.0.1:{studio.server_port}", "X-AssetFurnace-Request": "studio",
                           "Content-Type": "multipart/form-data; boundary=x",
                           "Content-Length": str(layers.UPLOAD_LIMIT + 64 * 1024 + 1)}.items():
            conn.putheader(key, value)
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 413 and "20 MB" in json.loads(response.read())["error"]
    finally:
        conn.close()
    with pytest.raises(ValueError, match="larger than 20 MB"):
        layers.add_from_file(Path("/nowhere"), "big.wav", b"x" * (layers.UPLOAD_LIMIT + 1))


def test_a_kept_sound_can_be_a_layer_and_keeps_its_own_licence(studio, tmp_path):
    _, started, _ = call(studio, "POST", "/api/sound", ASK)
    threading.Event().wait(0.2)
    _, kept, _ = call(studio, "POST", f"/api/sound/{started['job_id']}/keep", {"try": 1})
    sound_id = kept["file"].split("/")[1]
    status, layer, _ = call(studio, "POST", "/api/sound/layers/add", {"sound_id": sound_id})
    assert status == 200 and layer["from"] == "kept" and layer["licence"] == "Apache-2.0"
    (tmp_path / "output" / kept["file"]).with_name("sound.provenance.json").unlink()
    _, bare, _ = call(studio, "POST", "/api/sound/layers/add", {"sound_id": sound_id})
    assert bare["licence"] == "Not checked" and bare["checked"] is False       # no record, no claim


@pytest.mark.parametrize("body", [
    {"sound_id": "../../etc"}, {"sound_id": "nope__20261120-101010"}, {"job_id": "0" * 32, "try": 1},
    {"job_id": "../x", "try": 1},
])
def test_a_layer_from_something_that_is_not_there_is_not_found(studio, body):
    assert call(studio, "POST", "/api/sound/layers/add", body)[0] == 404


def test_add_needs_to_know_which_try(studio):
    status, told, _ = call(studio, "POST", "/api/sound/layers/add", {"job_id": "0" * 32, "try": "1"})
    assert status == 422 and "which try" in told["error"]


# ------------------------------------------------------------------ play together, save as one

def test_play_together_answers_one_sound_made_by_the_real_mix(studio):
    first = made_try(studio, 1)
    _, second, _ = upload(studio, wav_file(tone(0.3, 880)), licence="mine")
    stack = {"layers": [{"layer": first["layer"]},
                        {"layer": second["layer"], "start_ms": 250, "volume_db": -6, "pitch_semitones": 12}]}
    status, told, _ = call(studio, "POST", "/api/sound/layers/mix", stack)
    assert status == 200, told
    assert told["seconds"] == 0.4 and len(told["bars"]) == 90            # 250 ms + 300 ms an octave up (150 ms)
    assert told["layers"] == [{"start_s": 0.0, "seconds": 0.2}, {"start_s": 0.25, "seconds": 0.15}]
    assert told["licence"] == "Apache-2.0 + Your own work" and told["checked"] is True
    status, wav, kind = call(studio, "GET", told["url"])
    assert status == 200 and kind == "audio/wav"
    with wave.open(io.BytesIO(wav)) as heard:
        assert (heard.getframerate(), heard.getnchannels(), heard.getnframes()) == (48_000, 1, 19_200)
    assert call(studio, "GET", "/api/sound/layers/mix/" + "0" * 32 + ".wav")[0] == 404
    assert call(studio, "GET", "/api/sound/kept")[1]["sounds"] == []     # hearing it kept nothing


def stack_of(studio):
    first = made_try(studio, 1)
    _, second, _ = upload(studio, wav_file(tone(0.3, 880)), "thud.wav", licence="cc0", link="https://freesound.org/s/1/")
    return {"name": "Arrow hit", "layers": [
        {"layer": first["layer"], "start_ms": 0, "volume_db": 0, "pitch_semitones": 0},
        {"layer": second["layer"], "start_ms": 250, "volume_db": -3, "pitch_semitones": -2}]}


def test_save_as_one_sound_writes_a_kept_sound_with_every_layer_beside_it(studio, tmp_path):
    stack = stack_of(studio)
    status, saved, _ = call(studio, "POST", "/api/sound/layers/save", stack)
    assert status == 200, saved
    assert saved["name"] == "Arrow-hit" and saved["layers"] == 2 and saved["licence"] == "Apache-2.0 + CC0"
    folder = tmp_path / "output" / "sounds" / saved["id"]
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == [
        "layers/layer-1.wav", "layers/layer-2.wav", "sound.provenance.json", "sound.wav"]
    record = json.loads((folder / "sound.provenance.json").read_text())
    facts = sound_post.inspect_wav(folder / "sound.wav")
    assert record["kind"] == "sound" and record["schema_version"] == 1
    assert record["output"] == {"path": "sound.wav", "sha256": facts["sha256"], "sample_rate": 48_000, "channels": 1,
                                "seconds": facts["seconds"], "classification": "commercial-clear"}
    assert record["license"]["name"] == "Apache-2.0 + CC0" and record["license"]["classification"] == "commercial-clear"
    one, two = record["layers"]
    assert (one["id"], one["file"], one["source"]["type"], one["source"]["prompt"]) == (
        "layer-1", "layers/layer-1.wav", "generated", ASK["prompt"])
    assert (two["start_ms"], two["volume_db"], two["pitch_semitones"]) == (250.0, -3.0, -2.0)
    assert two["source"]["type"] == "file" and two["source"]["link"] == "https://freesound.org/s/1/"
    assert two["license"]["name"] == "CC0" and len(two["source"]["sha256"]) == 64
    for layer in record["layers"]:
        assert sound_post.sha256_file(folder / layer["file"]) == layer["sha256"]
    assert record["post"]["mix"] == "sum, then one turn-down" and record["post"]["gain_db"] <= 0
    assert record["loudness"]["peak"] == facts["peak"] <= 0.98 and record["loudness"]["measured"] < 0
    # the file is the real mix of those layer files with those numbers
    again, _ = sound_post.mix_layers([
        {"samples": sound_post.read_wav(folder / layer["file"])[0], **{k: layer[k] for k in sound_post.LAYER_LIMITS}}
        for layer in record["layers"]])
    assert np.allclose(sound_post.read_wav(folder / "sound.wav")[0], again, atol=1e-4)
    assert not [p for p in (tmp_path / "output/sounds").iterdir() if p.name.startswith(".mix-")]


def test_a_saved_stack_works_wherever_a_kept_sound_does(studio, tmp_path):
    _, saved, _ = call(studio, "POST", "/api/sound/layers/save", stack_of(studio))
    (listed,) = call(studio, "GET", "/api/sound/kept")[1]["sounds"]
    assert listed["id"] == saved["id"] and listed["layers"] == 2 and listed["licence"] == "Apache-2.0 + CC0"
    assert listed["file"] == saved["file"] and listed["kind"] == "sound"
    status, body, _ = call(studio, "GET", "/output/" + saved["file"])
    assert status == 200 and body[:4] == b"RIFF"
    status, takes, _ = call(studio, "POST", f"/api/sounds/{saved['id']}/variations", {"count": 3, "seed": 1})
    assert status == 200 and len(takes["variations"]) == 3
    status, made, _ = call(studio, "POST", f"/api/sounds/{saved['id']}/set", {"count": 3, "seed": 1})
    assert status == 200 and made["licence"] == "Apache-2.0 + CC0"
    import footsteps_api
    steps = footsteps_api.step_sound(tmp_path / "output", saved["id"], tmp_path / "output")
    assert steps["license"]["name"] == "Apache-2.0 + CC0" and steps["files"] == [saved["file"]]


def test_one_not_checked_layer_makes_the_whole_sound_not_checked(studio, tmp_path):
    stack = stack_of(studio)
    _, unknown, _ = upload(studio, wav_file(tone(0.1, 200)), "found.wav")
    stack["layers"].append({"layer": unknown["layer"], "start_ms": 100})
    told = call(studio, "POST", "/api/sound/layers/mix", stack)[1]
    assert told["checked"] is False and told["licence"] == "Apache-2.0 + CC0 + Not checked"
    _, saved, _ = call(studio, "POST", "/api/sound/layers/save", stack)
    record = json.loads((tmp_path / "output/sounds" / saved["id"] / "sound.provenance.json").read_text())
    assert record["output"]["classification"] == "unverified" and record["license"]["verified"] is False
    assert record["layers"][2]["license"]["name"] == "Not checked" and saved["checked"] is False


def test_two_layers_under_the_same_licence_name_it_once(studio):
    first, second = made_try(studio, 1), None
    _, started, _ = call(studio, "POST", "/api/sound", ASK)
    threading.Event().wait(0.2)
    _, second, _ = call(studio, "POST", "/api/sound/layers/add", {"job_id": started["job_id"], "try": 2})
    told = call(studio, "POST", "/api/sound/layers/mix",
                {"layers": [{"layer": first["layer"]}, {"layer": second["layer"], "start_ms": 100}]})[1]
    assert told["licence"] == "Apache-2.0" and told["checked"] is True


def test_credit_needed_beside_the_engine_is_mixed_classes_so_unverified_by_the_repos_rule(studio):
    first = made_try(studio, 1)
    _, by, _ = upload(studio, wav_file(tone()), licence="cc-by", link="https://freesound.org/s/2/")
    told = call(studio, "POST", "/api/sound/layers/mix", {"layers": [{"layer": first["layer"]}, {"layer": by["layer"]}]})[1]
    assert told["licence"] == "Apache-2.0 + CC-BY" and told["checked"] is False


@pytest.mark.parametrize("change, why", [
    ({"start_ms": -5}, "Starts at must be between 0 and 10000 ms"),
    ({"volume_db": 40}, "Volume must be between -40 and 12 dB"),
    ({"pitch_semitones": 24}, "Pitch must be between -12 and 12 semitones"),
    ({"volume_db": "loud"}, "Volume must be a number"),
])
def test_a_value_out_of_range_is_refused_and_nothing_is_saved(studio, tmp_path, change, why):
    layer = made_try(studio, 1)
    for action in ("mix", "save"):
        status, told, _ = call(studio, "POST", f"/api/sound/layers/{action}", {"layers": [{"layer": layer["layer"], **change}]})
        assert status == 422 and why in told["error"]
    assert call(studio, "GET", "/api/sound/kept")[1]["sounds"] == []


@pytest.mark.parametrize("stack, status, why", [
    ({"layers": []}, 422, "add a layer first"),
    ({}, 422, "add a layer first"),
    ({"layers": ["x"]}, 422, "each layer"),
    ({"layers": [{"layer": "../../../etc/passwd"}]}, 404, "no such layer"),
    ({"layers": [{"layer": "0" * 32}]}, 404, "no longer here"),
    ({"layers": [{"layer": "0" * 32}] * 9}, 422, "up to 8 layers"),
])
def test_a_stack_that_makes_no_sense_is_refused(studio, stack, status, why):
    for action in ("mix", "save"):
        got, told, _ = call(studio, "POST", f"/api/sound/layers/{action}", stack)
        assert got == status and why in told["error"]


@pytest.mark.parametrize("path", ["/api/sound/layers/../../../etc/passwd.wav", "/api/sound/layers/" + "0" * 32 + ".wav",
                                  "/api/sound/layers/x.txt", "/api/sound/layers/mix/..%2F..%2Fx.wav"])
def test_a_layer_or_mix_address_cannot_reach_anything_else(studio, path):
    assert call(studio, "GET", path)[0] == 404


def test_the_workbench_is_never_served_as_plain_files(studio):
    layer = made_try(studio, 1)
    assert call(studio, "GET", f"/output/sounds/.layers/{layer['layer']}/layer.wav")[0] == 404


def test_layers_left_for_a_week_are_cleared_and_fresh_ones_stay(studio, tmp_path):
    old, fresh = made_try(studio, 1), made_try(studio, 2)
    bench = tmp_path / "output/sounds/.layers"
    long_ago = time.time() - layers.STALE_SECONDS - 60
    os.utime(bench / old["layer"], (long_ago, long_ago))
    upload(studio, wav_file(tone()))
    assert not (bench / old["layer"]).exists() and (bench / fresh["layer"]).is_dir()


# ------------------------------------------------------------------ edit layers

def test_a_saved_stack_opens_again_with_each_layer_where_it_was(studio, tmp_path):
    stack = stack_of(studio)
    _, saved, _ = call(studio, "POST", "/api/sound/layers/save", stack)
    status, opened, _ = call(studio, "POST", f"/api/sounds/{saved['id']}/layers", {})
    assert status == 200 and opened["name"] == "Arrow-hit" and opened["title"] == "Arrow hit"
    assert [(l["start_ms"], l["volume_db"], l["pitch_semitones"]) for l in opened["layers"]] == [(0.0, 0.0, 0.0), (250.0, -3.0, -2.0)]
    assert [l["licence"] for l in opened["layers"]] == ["Apache-2.0", "CC0"]
    assert {l["layer"] for l in opened["layers"]}.isdisjoint(l["layer"] for l in stack["layers"])     # fresh copies
    again = {"name": "Arrow hit", "layers": [{k: l[k] for k in ("layer", "start_ms", "volume_db", "pitch_semitones")}
                                             for l in opened["layers"]]}
    _, resaved, _ = call(studio, "POST", "/api/sound/layers/save", again)
    assert resaved["id"] != saved["id"] and resaved["licence"] == "Apache-2.0 + CC0"                  # a new sound
    first = sound_post.read_wav(tmp_path / "output" / saved["file"])[0]
    second = sound_post.read_wav(tmp_path / "output" / resaved["file"])[0]
    assert np.allclose(first, second, atol=1e-4)                       # same layers, same numbers, same sound


def test_a_sound_kept_from_one_try_has_no_stack_to_edit(studio):
    _, started, _ = call(studio, "POST", "/api/sound", ASK)
    threading.Event().wait(0.2)
    _, kept, _ = call(studio, "POST", f"/api/sound/{started['job_id']}/keep", {"try": 1})
    sound_id = kept["file"].split("/")[1]
    assert call(studio, "GET", "/api/sound/kept")[1]["sounds"][0]["layers"] == 0
    status, told, _ = call(studio, "POST", f"/api/sounds/{sound_id}/layers", {})
    assert status == 404 and "not built from layers" in told["error"]
    assert call(studio, "POST", "/api/sounds/..%2F..%2Fx/layers", {})[0] == 404


def test_a_layer_file_that_points_elsewhere_is_not_a_stack(tmp_path):
    folder = tmp_path / "a__20261120-101010"
    (folder / "layers").mkdir(parents=True)
    (folder / "layers" / "layer-1.wav").write_bytes(b"x")
    assert len(sound_api.kept_layer_files(folder, {"layers": [{"file": "layers/layer-1.wav"}]})) == 1
    for file in ("sound.wav", "layers/../../secret.wav", "/etc/passwd", "layers/layer-2.wav", None):
        assert sound_api.kept_layer_files(folder, {"layers": [{"file": "layers/layer-1.wav"}, {"file": file}]}) == []


# ------------------------------------------------------------------ split, and the door

def test_split_answers_layers_to_make(studio):
    status, told, _ = call(studio, "POST", "/api/sound/layers/split", {"description": "arrow hits a wooden target"})
    assert status == 200 and told["rule"] == "thrown-hit"
    assert [l["describe"] for l in told["layers"]] == ["arrow whoosh past, fast air, dry", "arrow thudding into wood, one hit, dry"]
    assert call(studio, "POST", "/api/sound/layers/split", {"description": "coin"})[1]["rule"] is None
    assert call(studio, "POST", "/api/sound/layers/split", {})[0] == 422


def test_the_sheet_is_told_what_layers_allow(studio):
    _, defaults, _ = call(studio, "GET", "/api/sound/defaults")
    assert defaults["layers"] == layers.options_payload()


POSTS = ["/api/sound/layers/split", "/api/sound/layers/add", "/api/sound/layers/mix", "/api/sound/layers/save",
         "/api/sound/layers/upload", "/api/sounds/a__20261120-101010/layers"]


@pytest.mark.parametrize("path", POSTS)
def test_a_page_on_another_site_cannot_touch_layers(studio, path):
    assert call(studio, "POST", path, {}, headers={"Origin": "https://evil.example"})[0] == 403
    assert call(studio, "POST", path, {}, headers={"X-AssetFurnace-Request": ""})[0] == 403


@pytest.mark.parametrize("path", POSTS + ["/api/sound/layers/" + "0" * 32 + ".wav", "/api/sound/layers/mix/" + "0" * 32 + ".wav"])
def test_a_keyed_studio_wants_its_key_for_layers_too(studio, monkeypatch, path):
    monkeypatch.setattr(generate_api.Handler, "studio_token", "s3cret")
    method = "GET" if path.endswith(".wav") else "POST"
    assert call(studio, method, path, None if method == "GET" else {})[0] == 401
