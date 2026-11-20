"""Tests for "Make a sound": viewer/sound_api.py and its runner, scripts/sound_generate.py.

The model is about 6 GB and lives in its own environment, so it is never loaded here. The
one call into it is replaced by a stand-in that answers the same shape the real pipeline
does ((1, 1, frames) at 48 kHz); everything around that call is the shipped code.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "scripts"), str(REPO / "viewer")]

import sound_api as api
import sound_generate as runner

from image_to_3dlab import sound_post
from image_to_3dlab.provenance import LICENSES

ASK = {"prompt": "  Sword striking a wooden shield,\n one hit, dry ", "seconds": 1.5, "seed": 7,
       "leave_out": ["music", "voices"]}


class FakePipe:
    """Stands in for MossSoundEffectPipeline: same call, same answer shape, no model."""

    sample_rate = 48_000

    def __init__(self, silent_seeds=()):
        self.calls = []
        self.silent_seeds = set(silent_seeds)

    def __call__(self, prompt, seconds, num_inference_steps, cfg_scale, seed, negative_prompt,
                 progress_bar_cmd):
        self.calls.append({"prompt": prompt, "seconds": seconds, "steps": num_inference_steps,
                           "cfg": cfg_scale, "seed": seed, "negative": negative_prompt})
        for _ in progress_bar_cmd(range(num_inference_steps)):
            pass
        frames = int(self.sample_rate * seconds)
        t = np.arange(frames) / self.sample_rate
        tone = 1.6 * np.sin(2 * np.pi * (200 + 50 * seed) * t) * np.exp(-4 * t)     # too loud, on purpose
        tone[: frames // 10] = 0.0                                                # dead air at the start
        if seed in self.silent_seeds:
            tone[:] = 0.0
        return tone.astype(np.float32).reshape(1, 1, -1)


def weights_folder(tmp_path, engine=api.MOSS, leave=()):
    folder = tmp_path / "weights"
    for name in engine.needs:
        if name not in leave:
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(b"x")
    return folder


# ----------------------------------------------------------------- the engine table and limits

def test_the_engine_is_what_its_docs_say():
    moss = api.MOSS
    assert (moss.sample_rate, moss.channels, moss.max_seconds) == (48_000, 1, 30.0)
    assert moss.leave_out is True                       # it takes a negative prompt
    assert moss.repo == "mlx-community/MOSS-SoundEffect-v2.0-4bit" and len(moss.revision) == 40
    assert LICENSES[moss.licence_key].license_name == "Apache-2.0"
    assert LICENSES[moss.licence_key].classification == "commercial-clear"
    assert moss.sample_rate == sound_post.RATE and moss.channels == sound_post.CHANNELS
    assert "vendor/moss-sfx" in moss.python.as_posix()  # its own environment, not the main one


def test_a_good_request_comes_back_tidy():
    request = api.clean_request(ASK)
    assert request == {"kind": "effect", "prompt": "Sword striking a wooden shield, one hit, dry",
                       "seconds": 1.5, "steps": 100, "guidance": 4.0, "count": 4, "seed": 7,
                       "leave_out": ["music", "voices"], "name": "Sword-striking-a-wooden-shield-one-hit-dry"}


@pytest.mark.parametrize("change,why", [
    ({"seconds": 45}, "0.5 to 30 seconds"),
    ({"seconds": 0.2}, "0.5 to 30 seconds"),
    ({"seconds": "long"}, "length must be a number"),
    ({"seconds": float("nan")}, "0.5 to 30 seconds"),
    ({"seconds": True}, "length must be a number"),
    ({"prompt": "   "}, "describe the sound"),
    ({"prompt": "x" * 501}, "longer than 500"),
    ({"count": 5}, "between 1 and 4"),
    ({"count": 0}, "between 1 and 4"),
    ({"count": 2.5}, "whole number"),
    ({"steps": 5}, "between 10 and 200"),
    ({"steps": 500}, "between 10 and 200"),
    ({"seed": -1}, "seed must be between"),
    ({"seed": 2**31}, "seed must be between"),
    ({"seed": "abc"}, "seed must be a number"),
    ({"leave_out": ["x"] * 0 + [str(i) for i in range(9)]}, "at most 8"),
    ({"leave_out": ["y" * 41]}, "a few words each"),
    ({"leave_out": 5}, "list of things"),
    ({"kind": "music"}, "Music is not built yet"),
    ({"kind": "noise"}, "no sound kind"),
])
def test_anything_past_a_limit_is_refused_not_clipped(change, why):
    with pytest.raises(ValueError, match=why):
        api.clean_request({**ASK, **change})


def test_the_edges_of_the_limits_are_allowed():
    assert api.clean_request({**ASK, "seconds": 0.5})["seconds"] == 0.5
    assert api.clean_request({**ASK, "seconds": "30"})["seconds"] == 30.0
    assert api.clean_request({**ASK, "count": 1, "steps": 10})["count"] == 1


def test_leave_out_is_tidied_and_takes_a_comma_list():
    assert api.clean_request({**ASK, "leave_out": " music , , room   echo,music"})["leave_out"] == ["music", "room echo"]
    assert api.clean_request({**ASK, "leave_out": None})["leave_out"] == []


def test_an_engine_with_no_negative_prompt_refuses_leave_out():
    import dataclasses
    deaf = dataclasses.replace(api.MOSS, leave_out=False)
    with pytest.raises(ValueError, match="cannot be told what to leave out"):
        api.clean_request(ASK, deaf)
    assert api.clean_request({**ASK, "leave_out": []}, deaf)["leave_out"] == []


def test_an_unknown_engine_is_named():
    with pytest.raises(ValueError, match="no sound engine 'tango'"):
        api.pick_engine("tango")
    assert api.pick_engine() is api.MOSS and api.pick_engine("moss") is api.MOSS


# ----------------------------------------------------------------------------- names and seeds

@pytest.mark.parametrize("given,name", [
    ("Sword on shield!", "Sword-on-shield"),
    ("../../etc/passwd", "etc-passwd"),
    ("a__b", "a_b"),                       # `__` is the folder's own separator
    ("   ", "sound"),
    (None, "sound"),
    ("ünïcödé ✨", "n-c-d"),
    ("x" * 200, "x" * 60),
    ("--weird--", "weird"),
])
def test_names_are_safe_for_a_folder(given, name):
    assert api.safe_name(given) == name
    assert api.KEPT_RUN.match(f"{api.safe_name(given)}__20261120-101500")


def test_one_seed_per_batch_and_each_try_counts_up_from_it():
    assert [api.try_seed(7, n) for n in (1, 2, 3, 4)] == [7, 8, 9, 10]


def test_a_seed_is_picked_when_none_is_given_and_leaves_room_for_the_tries():
    seeds = {api.clean_request({"prompt": "a door"})["seed"] for _ in range(20)}
    assert len(seeds) > 1 and all(0 <= s <= api.MAX_SEED for s in seeds)
    assert api.try_seed(api.MAX_SEED, api.MAX_TRIES) < 2**31


# ------------------------------------------------------------------------------ missing weights

def test_with_nothing_installed_it_says_what_is_missing_and_points_to_setup(tmp_path):
    absent = api.missing(api.MOSS, hub=tmp_path)
    assert "its model files (6.4 GB)" in absent
    with pytest.raises(api.MissingSound, match="not set up yet.*Open Setup"):
        api.check_ready(api.MOSS, hub=tmp_path)
    assert api.is_installed(api.MOSS, hub=tmp_path) is False


def test_a_half_finished_download_is_not_installed(tmp_path):
    snapshot = tmp_path / "models--mlx-community--MOSS-SoundEffect-v2.0-4bit" / "snapshots" / "abc"
    (snapshot / "mlx").mkdir(parents=True)
    (snapshot / "mlx" / "dit.safetensors").write_bytes(b"x")
    absent = api.missing(api.MOSS, hub=tmp_path)
    assert "mlx/vae.safetensors" in absent and "mlx/dit.safetensors" not in absent


def test_the_runner_refuses_before_loading_when_weights_are_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-hub"))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    loaded = []
    with pytest.raises(SystemExit) as stop:
        runner.main(["--prompt", "a door", "--out", str(tmp_path / "out")], load=loaded.append)
    said = str(stop.value)
    assert "MOSS-SoundEffect v2.0" in said and "4-bit MLX" in said and "6.4 GB" in said
    assert "does not fetch" in said
    assert loaded == [] and not (tmp_path / "out").exists()


def test_the_runner_refuses_a_half_finished_download(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    folder = weights_folder(tmp_path, leave=("mlx/vae.safetensors",))
    with pytest.raises(SystemExit, match="not finished. Missing: mlx/vae.safetensors"):
        runner.local_weights(api.MOSS, str(folder))


def test_the_runner_pins_itself_offline_once_the_weights_are_found(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    folder = weights_folder(tmp_path)
    assert runner.local_weights(api.MOSS, str(folder)) == folder
    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_the_runner_refuses_a_request_past_the_limits_before_anything_else(tmp_path):
    with pytest.raises(SystemExit, match="0.5 to 30 seconds"):
        runner.main(["--prompt", "a door", "--seconds", "60"], load=lambda w: pytest.fail("loaded the model"))


# ------------------------------------------------------------------------- the runner, one sound

def test_one_sound_is_cleaned_checked_and_written_with_its_record(tmp_path):
    request = api.clean_request({**ASK, "count": 1})
    pipe, said = FakePipe(), []
    facts = runner.make_one(pipe, api.MOSS, request, 1, tmp_path / "s", lambda event, **f: said.append((event, f)))

    assert pipe.calls == [{"prompt": request["prompt"], "seconds": 1.5, "steps": 100, "cfg": 4.0,
                           "seed": 7, "negative": "music, voices"}]
    assert [f["step"] for e, f in said if e == "step"] == list(range(1, 101))
    wav = tmp_path / "s" / "sound.wav"
    on_disk = sound_post.inspect_wav(wav)                       # ai-sfx's assert_wavs, for one file
    assert wav.stat().st_size > 44
    assert on_disk["sample_rate"] == 48_000 and on_disk["channels"] == 1
    assert on_disk["peak"] <= 0.98 + 1e-3                       # the too-loud tone was turned down
    assert on_disk["seconds"] < 1.5                             # the dead air was cut
    assert facts["seconds"] == on_disk["seconds"] and facts["seed"] == 7 and len(facts["bars"]) == 60

    record = json.loads((tmp_path / "s" / "sound.provenance.json").read_text())
    assert record["output"]["sha256"] == on_disk["sha256"]


def test_the_record_has_the_shape_later_slices_build_on(tmp_path):
    request = api.clean_request({**ASK, "count": 4})
    runner.make_one(FakePipe(), api.MOSS, request, 3, tmp_path, lambda *a, **k: None)
    record = json.loads((tmp_path / "sound.provenance.json").read_text())

    assert {"schema_version", "kind", "output", "license", "layers", "variation", "post", "loudness",
            "loop", "made_for", "made_with"} <= set(record)
    assert record["schema_version"] == 1 and record["kind"] == "sound" and record["sound_kind"] == "effect"
    assert record["variation"] is None and record["loop"] is None and record["made_for"] is None
    out = record["output"]
    assert set(out) == {"path", "sha256", "sample_rate", "channels", "seconds", "classification"}
    assert (out["path"], out["sample_rate"], out["channels"], out["classification"]) == (
        "sound.wav", 48_000, 1, "commercial-clear")
    assert record["post"] == {"trim": True, "fade_ms": 5.0, "peak": 0.98}
    loudness = record["loudness"]                               # the peak is the file's own, after the fades
    assert set(loudness) == {"target", "measured", "peak"} and loudness["target"] is None
    assert loudness["measured"] is None and 0.5 < loudness["peak"] <= 0.98 + 1e-3
    assert record["license"]["name"] == "Apache-2.0" and record["license"]["classification"] == "commercial-clear"
    assert any("training data is not published" in term for term in record["license"]["conditions"])

    (layer,) = record["layers"]                                 # a single effect is one layer
    assert set(layer) == {"id", "source", "file", "sha256", "license", "start_ms", "volume_db", "pitch_semitones"}
    assert (layer["file"], layer["sha256"]) == ("sound.wav", out["sha256"])
    assert (layer["start_ms"], layer["volume_db"], layer["pitch_semitones"]) == (0, 0.0, 0)
    source = layer["source"]
    assert source["type"] == "generated" and source["engine"] == "moss"
    assert source["prompt"] == request["prompt"] and source["steps"] == 100
    assert (source["batch_seed"], source["try"], source["of"], source["seed"]) == (7, 3, 4, 9)
    assert source["leave_out"] == ["music", "voices"] and source["revision"] == api.MOSS.revision
    assert record["made_with"]["name"] == "AssetFurnace"


def test_mixed_licences_follow_the_rule_derived_files_already_use():
    apache = {"license": {"name": "Apache-2.0", "classification": "commercial-clear", "conditions": ["a"]}}
    other = {"license": {"name": "CC-BY-4.0", "classification": "conditional", "conditions": ["credit", "a"]}}
    assert api.combined_licence([apache]) == {"name": "Apache-2.0", "classification": "commercial-clear", "conditions": ["a"]}
    same = api.combined_licence([apache, apache])
    assert same["name"] == "Apache-2.0 + Apache-2.0" and same["classification"] == "commercial-clear"
    mixed = api.combined_licence([apache, other])
    assert mixed == {"name": "Apache-2.0 + CC-BY-4.0", "conditions": ["a", "credit"],
                     "classification": "unverified", "verified": False}
    unknown = api.combined_licence([apache, {"file": "mine.wav"}])     # a file with no licence stated
    assert unknown["classification"] == "unverified" and unknown["verified"] is False


def test_a_batch_makes_each_try_in_its_own_folder_from_its_own_seed(tmp_path):
    request = api.clean_request({**ASK, "count": 4})
    pipe, said = FakePipe(), []
    made = runner.make_batch(pipe, api.MOSS, request, tmp_path, lambda event, **f: said.append((event, f)))
    assert [m["seed"] for m in made] == [7, 8, 9, 10] == [c["seed"] for c in pipe.calls]
    assert [Path(m["file"]).relative_to(tmp_path).as_posix() for m in made] == [f"try-{n}/sound.wav" for n in (1, 2, 3, 4)]
    hashes = {sound_post.sha256_file(Path(m["file"])) for m in made}
    assert len(hashes) == 4                                     # four different sounds
    assert [f["number"] for e, f in said if e == "try"] == [1, 2, 3, 4]


def test_a_single_sound_lands_in_the_folder_itself(tmp_path):
    made = runner.make_batch(FakePipe(), api.MOSS, api.clean_request({**ASK, "count": 1}), tmp_path, lambda *a, **k: None)
    assert [Path(m["file"]) for m in made] == [tmp_path / "sound.wav"]


def test_a_silent_try_is_reported_and_skipped_not_written(tmp_path):
    request = api.clean_request({**ASK, "count": 3})
    said = []
    made = runner.make_batch(FakePipe(silent_seeds={8}), api.MOSS, request, tmp_path,
                             lambda event, **f: said.append((event, f)))
    assert [m["number"] for m in made] == [1, 3]
    assert not (tmp_path / "try-2" / "sound.wav").exists()
    assert [(f["number"], f["error"]) for e, f in said if e == "problem"] == [(2, "Try 2: the sound is silent.")]


def test_the_step_reporter_passes_every_step_through():
    said = []
    wrap = runner.step_reporter(2, lambda event, **f: said.append(f))
    assert list(wrap([10, 20, 30])) == [10, 20, 30]
    assert said == [{"number": 2, "step": n, "total": 3} for n in (1, 2, 3)]


# ---------------------------------------------------------------------------------- the command

def test_the_command_runs_the_runner_in_the_engines_own_environment(tmp_path):
    request = api.clean_request({**ASK, "prompt": "a door; rm -rf ~ --seconds 99"})
    command = api.build_command(request, tmp_path / "out", api.MOSS, tmp_path / "w")
    assert command[0] == str(api.MOSS.python) and command[1] == str(REPO / "scripts" / "sound_generate.py")
    assert command[command.index("--prompt") + 1] == "a door; rm -rf ~ --seconds 99"   # one argument, not shell text
    assert command[command.index("--seconds") + 1] == "1.5"
    assert command[command.index("--seed") + 1] == "7" and command[command.index("--count") + 1] == "4"
    assert [command[i + 1] for i, part in enumerate(command) if part == "--leave-out"] == ["music", "voices"]
    assert command[command.index("--model-dir") + 1] == str(tmp_path / "w")


def test_runner_lines_are_events_and_chatter_is_not():
    assert api.read_event('{"event":"try","number":1}') == {"event": "try", "number": 1}
    assert api.read_event("Loading weights...") is None
    assert api.read_event('{"not": "an event"}') is None and api.read_event("{broken") is None
    assert api.failure_reason(['{"event":"loading"}', "ValueError: no", '{"event":"step","step":1}']) == "ValueError: no"


# -------------------------------------------------------------------------------------- the job

STAND_IN = """
    import sys
    sys.path[:0] = [{repo!r}, {tests!r}]
    import sound_generate
    from test_sound_api import FakePipe
    raise SystemExit(sound_generate.main(sys.argv[1:], load=lambda weights: FakePipe(silent_seeds={silent!r})))
"""


def stand_in_command(tmp_path, job, weights, silent=(), crash=False):
    """The real runner, started as the studio starts it, with only the model replaced."""
    script = tmp_path / "stand_in.py"
    body = "raise RuntimeError('the model fell over')" if crash else textwrap.dedent(STAND_IN).format(
        repo=str(REPO / "scripts"), tests=str(REPO / "tests"), silent=set(silent))
    script.write_text(body, encoding="utf-8")
    real = api.build_command(job.request, job.directory, job.engine, weights)
    return [sys.executable, str(script), *real[2:]]


def test_only_one_batch_runs_at_a_time(tmp_path):
    manager = api.SoundJobManager(tmp_path)
    first = manager.create(api.clean_request(ASK))
    with pytest.raises(RuntimeError, match="already being made"):
        manager.create(api.clean_request(ASK))
    manager.finish(first)
    first.status = "done"
    assert manager.create(api.clean_request(ASK)).id != first.id


def test_make_4_makes_four_tries_and_keep_puts_one_in_the_library(tmp_path):
    manager = api.SoundJobManager(tmp_path / "sounds")
    job = manager.create(api.clean_request({**ASK, "steps": 10}))
    api.run_job(job, manager, stand_in_command(tmp_path, job, weights_folder(tmp_path)))

    assert job.status == "done" and job.error is None, job.describe()
    assert manager.active is None                               # the slot is free again
    told = job.describe()
    assert [t["number"] for t in told["tries"]] == [1, 2, 3, 4]
    assert [t["seed"] for t in told["tries"]] == [7, 8, 9, 10] and told["seed"] == 7
    assert all(len(t["bars"]) == 60 and t["seconds"] > 0 and t["kept"] is None for t in told["tries"])
    assert told["tries"][0]["url"] == f"/api/sound/{job.id}/try-1.wav"
    assert all(job.try_file(n).is_file() for n in (1, 2, 3, 4))
    assert job.directory.parent.name == ".tries" and not (job.directory / "pid").exists()
    phases = [e.get("phase") for e in job.events if e.get("phase")]
    assert phases[0] == "loading" and phases.count("try") == 4 and "making" in phases
    percents = [e["percent"] for e in job.events if e.get("phase") == "making"]
    assert percents == sorted(percents) and percents[-1] == 100.0
    assert api.kept_sounds(tmp_path) == []                      # a try is not in the Library

    kept = manager.keep(job.id, 2)
    folder = tmp_path / kept["file"]
    assert folder.parent.parent == tmp_path / "sounds" and api.KEPT_RUN.match(folder.parent.name)
    assert folder.parent.name.startswith("Sword-striking-a-wooden-shield-one-hit-dry__")
    record = json.loads((folder.parent / "sound.provenance.json").read_text())
    assert record["output"]["sha256"] == sound_post.inspect_wav(folder)["sha256"]
    assert record["layers"][0]["source"]["seed"] == 8
    assert manager.keep(job.id, 2) == kept                      # kept once, however often it is pressed
    assert job.describe()["tries"][1]["kept"] == kept["file"]

    (listed,) = api.kept_sounds(tmp_path)
    assert listed["file"] == kept["file"] and listed["kind"] == "sound" and listed["sound_kind"] == "effect"
    assert listed["name"] == "Sword striking a wooden shield one hit dry"
    assert listed["prompt"] == job.request["prompt"] and listed["licence"] == "Apache-2.0"
    assert listed["seconds"] == record["output"]["seconds"]

    with pytest.raises(ValueError, match="no such try"):
        manager.keep(job.id, 9)
    with pytest.raises(ValueError, match="no such try"):
        manager.keep("nope", 1)


def test_a_kept_sound_can_be_given_its_own_name(tmp_path):
    manager = api.SoundJobManager(tmp_path / "sounds")
    job = manager.create(api.clean_request({**ASK, "count": 1, "steps": 10}))
    api.run_job(job, manager, stand_in_command(tmp_path, job, weights_folder(tmp_path)))
    kept = manager.keep(job.id, 1, name="../Shield hit")
    assert kept["name"] == "Shield-hit" and kept["file"].startswith("sounds/Shield-hit__")


def test_the_next_batch_clears_tries_nobody_kept(tmp_path):
    manager = api.SoundJobManager(tmp_path / "sounds")
    first = manager.create(api.clean_request({**ASK, "count": 1, "steps": 10}))
    api.run_job(first, manager, stand_in_command(tmp_path, first, weights_folder(tmp_path)))
    kept = manager.keep(first.id, 1)
    second = manager.create(api.clean_request(ASK))
    assert not first.directory.exists() and second.directory.is_dir()
    assert (tmp_path / kept["file"]).is_file()                  # what was kept stays
    with pytest.raises(ValueError, match="no such try|no longer on disk"):
        manager.keep(first.id, 2)


def test_a_silent_try_does_not_sink_the_batch(tmp_path):
    manager = api.SoundJobManager(tmp_path / "sounds")
    job = manager.create(api.clean_request({**ASK, "count": 2, "steps": 10}))
    api.run_job(job, manager, stand_in_command(tmp_path, job, weights_folder(tmp_path), silent={8}))
    assert job.status == "done" and [t["number"] for t in job.tries] == [1]
    assert [e["note"] for e in job.events if e.get("phase") == "problem"] == ["Try 2: the sound is silent."]


def test_a_batch_with_no_usable_sound_says_why(tmp_path):
    manager = api.SoundJobManager(tmp_path / "sounds")
    job = manager.create(api.clean_request({**ASK, "count": 1, "steps": 10}))
    api.run_job(job, manager, stand_in_command(tmp_path, job, weights_folder(tmp_path), silent={7}))
    assert job.status == "error" and job.error == "Try 1: the sound is silent."
    assert manager.active is None


def test_a_crashed_engine_says_its_last_words(tmp_path):
    manager = api.SoundJobManager(tmp_path / "sounds")
    job = manager.create(api.clean_request(ASK))
    api.run_job(job, manager, stand_in_command(tmp_path, job, weights_folder(tmp_path), crash=True))
    assert job.status == "error" and job.error == "RuntimeError: the model fell over"


def test_a_job_with_the_engine_missing_asks_for_setup_and_starts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "check_ready", lambda engine: (_ for _ in ()).throw(api.MissingSound(["its model files (6.4 GB)"])))
    monkeypatch.setattr(api.subprocess, "Popen", lambda *a, **k: pytest.fail("started the engine"))
    manager = api.SoundJobManager(tmp_path / "sounds")
    job = manager.create(api.clean_request(ASK))
    api.run_job(job, manager)
    assert job.status == "error" and "Open Setup" in job.error
    assert job.events[-1]["needs_setup"] is True and manager.active is None


def test_cancel_only_reaches_a_running_job(tmp_path):
    manager = api.SoundJobManager(tmp_path / "sounds")
    job = manager.create(api.clean_request(ASK))
    assert manager.cancel(job.id) is True and job.status == "cancelling" and job.cancel_requested
    assert manager.cancel(job.id) is False and manager.cancel("nope") is False


def test_start_refuses_a_bad_request_before_a_job_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "MANAGER", api.SoundJobManager(tmp_path / "sounds"))
    with pytest.raises(ValueError, match="0.5 to 30 seconds"):
        api.start({**ASK, "seconds": 99})
    assert api.MANAGER.jobs == {} and not (tmp_path / "sounds").exists()


# ------------------------------------------------------------------------- what the sheet reads

def test_the_sheet_is_told_the_kinds_the_limits_and_whether_the_engine_is_here(monkeypatch):
    monkeypatch.setattr(api, "missing", lambda engine: ["its model files (6.4 GB)"])
    told = api.defaults_payload()
    assert told["installed"] is False and told["setup_id"] == "sound-effects" and told["download_size"] == "6.4 GB"
    assert [(k["label"], k["ready"]) for k in told["kinds"]] == [
        ("Effect", True), ("Background loop", False), ("Music", False), ("Voice", False)]
    assert told["leave_out"] == ["music", "voices", "room echo"]
    assert told["limits"] == {"min_seconds": 0.5, "max_seconds": 30.0, "default_seconds": 1.5,
                              "max_prompt": 500, "tries": 4}
    assert told["license"]["name"] == "Apache-2.0"
    monkeypatch.setattr(api, "missing", lambda engine: [])
    assert api.defaults_payload()["installed"] is True


def test_the_library_skips_tries_strays_and_folders_with_no_sound(tmp_path):
    sounds = tmp_path / "sounds"
    (sounds / ".tries" / "abc").mkdir(parents=True)
    (sounds / "not-a-run").mkdir()
    (sounds / "empty__20261120-101500").mkdir()
    bare = sounds / "old-hit__20261119-090000"
    bare.mkdir()
    sound_post.write_wav(bare / "sound.wav", np.full(480, 0.5, np.float32))
    (listed,) = api.kept_sounds(tmp_path)
    assert listed["id"] == "old-hit__20261119-090000" and listed["name"] == "Old hit"
    assert listed["licence"] is None and listed["prompt"] is None      # no record: nothing is claimed
    assert api.kept_sounds(tmp_path / "nowhere") == []
