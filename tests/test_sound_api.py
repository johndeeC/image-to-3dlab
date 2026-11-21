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

    def __init__(self, silent_seeds=(), shape=None):
        self.calls = []
        self.shape = shape                      # the sound to answer with, as a function of time
        self.silent_seeds = set(silent_seeds)

    def __call__(self, prompt, seconds, num_inference_steps, cfg_scale, seed, negative_prompt,
                 progress_bar_cmd, append_duration_suffix=True):
        self.calls.append({"prompt": prompt, "seconds": seconds, "steps": num_inference_steps,
                           "cfg": cfg_scale, "seed": seed, "negative": negative_prompt,
                           "suffix": append_duration_suffix})
        for _ in progress_bar_cmd(range(num_inference_steps)):
            pass
        frames = int(self.sample_rate * seconds)
        t = np.arange(frames) / self.sample_rate
        tone = 1.6 * np.sin(2 * np.pi * (200 + 50 * seed) * t) * np.exp(-4 * t)     # too loud, on purpose
        tone[: int(0.15 * self.sample_rate)] = 0.0                                # dead air at the start
        if seed in self.silent_seeds:
            tone[:] = 0.0
        if self.shape is not None:
            tone = self.shape(t)
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
    ({"kind": "music"}, "Music has its own page"),
    ({"kind": "voice"}, "Voice has its own page"),
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

    # the words carry the length asked for; the engine is asked for more, to leave room for a tail
    assert pipe.calls == [{"prompt": request["prompt"] + " duration: 1.5s", "seconds": 3.0, "steps": 100,
                           "cfg": 4.0, "seed": 7, "negative": "music, voices", "suffix": False}]
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
    assert record["post"] == {"trim": True, "fade_ms": 5.0, "peak": 0.98, "gain_db": record["post"]["gain_db"],
                              "tail_s": 0.0, "fade_out_ms": 5.0}
    assert isinstance(record["post"]["gain_db"], float)
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


def test_a_quiet_try_comes_out_as_loud_as_the_others(tmp_path):
    request = api.clean_request({**ASK, "count": 1})
    quiet = FakePipe(shape=lambda t: 0.25 * np.sin(2 * np.pi * 300 * t) * np.exp(-8 * t))
    facts = runner.make_one(quiet, api.MOSS, request, 1, tmp_path, lambda *a, **k: None)
    record = json.loads((tmp_path / "sound.provenance.json").read_text())
    assert facts["peak"] == pytest.approx(0.98, abs=2e-3)
    assert record["post"]["gain_db"] == pytest.approx(12.0, abs=0.5)
    assert record["loudness"]["peak"] == facts["peak"]


def test_a_ringing_try_runs_past_the_length_asked_and_the_record_says_so(tmp_path):
    request = api.clean_request({**ASK, "seconds": 1.0, "count": 1})
    bell = FakePipe(shape=lambda t: 0.6 * np.sin(2 * np.pi * 900 * t) * 0.5 ** (t / 0.15))
    facts = runner.make_one(bell, api.MOSS, request, 1, tmp_path, lambda *a, **k: None)
    record = json.loads((tmp_path / "sound.provenance.json").read_text())
    (call,) = bell.calls
    assert call["seconds"] == 2.0 and call["suffix"] is False and call["prompt"].endswith(" duration: 1.0s")
    assert 1.0 < facts["seconds"] <= 2.0                          # the real length, not the one asked
    assert record["output"]["seconds"] == facts["seconds"]
    assert record["layers"][0]["source"]["seconds_asked"] == 1.0
    assert record["post"]["tail_s"] == pytest.approx(facts["seconds"] - 1.0, abs=0.01)
    assert record["post"]["fade_out_ms"] == sound_post.TAIL_FADE_MS


def test_the_engine_is_never_asked_for_more_than_its_most(tmp_path):
    request = api.clean_request({**ASK, "seconds": 28.0, "count": 1, "steps": 10})
    pipe = FakePipe()
    runner.generate(pipe, request, 7, 1, lambda *a, **k: None, most=api.MOSS.max_seconds)
    assert pipe.calls[0]["seconds"] == 30.0 and pipe.calls[0]["prompt"].endswith(" duration: 28.0s")


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
        ("Effect", True), ("Background loop", True), ("Music", True), ("Voice", True)]
    loop = told["kinds"][1]
    assert loop["hint"] == "Plays round and round with no click. Pick it for wind, rain, engines."
    assert loop["seconds"] == {"min": 4.0, "max": 30.0, "default": 8.0}
    assert [k.get("sheet") for k in told["kinds"]] == [None, None, "music", "voice"]   # each has its own sheet
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


# ----------------------------------------------------------------------------- variation sets

SOURCE = "boots-on-gravel__20261120-091306"


def kept_boots(tmp_path, record=True, name=SOURCE):
    """A kept sound on disk, made the way the runner makes one: a decaying thump at 48 kHz."""
    root = tmp_path / "sounds"
    t = np.arange(24_000) / 48_000
    thump = (0.6 * np.sin(2 * np.pi * 180 * t) * np.exp(-9 * t)).astype(np.float32)
    wav = sound_post.write_wav(root / name / "sound.wav", sound_post.fade(thump))
    if record:
        request = api.clean_request({"prompt": "heavy boots on gravel, one step", "seed": 5, "count": 1})
        api.write_sidecar(wav, api.sidecar(engine=api.MOSS, request=request, number=1, wav=wav,
                                           facts=sound_post.inspect_wav(wav),
                                           post={"trim": True, "fade_ms": 5.0, "peak": 0.98}))
    return root


def test_a_variation_request_comes_back_tidy_with_a_seed():
    asked = api.clean_variation_request({"count": "4", "how_different": " A  Lot ", "change": "volume, pitch", "seed": 9})
    assert asked == {"count": 4, "how_different": "a lot", "change": ["pitch", "volume"], "seed": 9, "name": None}
    plain = api.clean_variation_request({})
    assert (plain["count"], plain["how_different"], plain["change"]) == (6, "a little", ["pitch", "volume"])
    assert 0 <= plain["seed"] <= api.MAX_SEED
    assert api.clean_variation_request({"name": "../Boot steps!"})["name"] == "Boot-steps"


@pytest.mark.parametrize("raw,why", [
    ({"count": 1}, "between 2 and 12"), ({"count": 13}, "between 2 and 12"), ({"count": 2.5}, "whole number"),
    ({"count": True}, "must be a number"), ({"how_different": "loads"}, "how different is one of: a little, some, a lot"),
    ({"change": []}, "pick what changes"), ({"change": ["reverb"]}, "pick what changes"),
    ({"change": 7}, "a list"), ({"seed": -1}, "seed must be between"), ({"seed": "x"}, "must be a number"),
])
def test_a_variation_request_past_a_limit_is_refused_not_clipped(raw, why):
    with pytest.raises(ValueError, match=why):
        api.clean_variation_request(raw)


def test_a_preview_lists_numbered_takes_and_writes_nothing(tmp_path):
    root = kept_boots(tmp_path)
    before = sorted(p.name for p in root.rglob("*"))
    told = api.variations_preview(root, SOURCE, {"count": 4, "how_different": "some", "seed": 3})
    assert sorted(p.name for p in root.rglob("*")) == before
    assert told["sound"] == SOURCE and told["title"] == "Boots on gravel" and told["seed"] == 3
    assert [v["file"] for v in told["variations"]] == [f"boots-on-gravel_0{n}.wav" for n in (1, 2, 3, 4)]
    first, second = told["variations"][:2]
    assert first["note"] == "the original" and first["seconds"] == 0.5 and len(first["bars"]) == 60
    assert second["pitch_semitones"] != 0 and second["note"] != "the original"
    assert second["url"] == (f"/api/sounds/{SOURCE}/variations/2.wav"
                             "?count=4&how_different=some&change=pitch,volume&seed=3")
    order = told["play_order"]
    assert len(order) == 32 and set(order) == {0, 1, 2, 3}
    assert all(a != b for a, b in zip(order, [*order[1:], order[0]], strict=True))
    assert api.variations_preview(root, SOURCE, {"count": 4, "how_different": "some", "seed": 3}) == told


def test_a_take_is_served_as_the_very_wav_a_save_would_write(tmp_path):
    root = kept_boots(tmp_path)
    ask = {"count": 3, "how_different": "a lot", "seed": 8}
    heard = [api.variation_wav(root, SOURCE, {k: str(v) for k, v in ask.items()}, n) for n in (1, 2, 3)]
    saved = api.save_variation_set(root, SOURCE, ask)
    assert [(tmp_path / f["file"]).read_bytes() for f in saved["files"]] == heard
    with pytest.raises(LookupError):
        api.variation_wav(root, SOURCE, ask, 4)
    with pytest.raises(ValueError, match="seed is missing"):
        api.variation_wav(root, SOURCE, {"count": 3}, 1)


def test_a_saved_set_is_numbered_files_and_one_record(tmp_path):
    root = kept_boots(tmp_path)
    saved = api.save_variation_set(root, SOURCE, {"count": 6, "how_different": "some", "seed": 21})
    folder = tmp_path / saved["folder"]
    assert api.KEPT_RUN.fullmatch(folder.name) and folder.name.startswith("boots-on-gravel_set__")
    assert sorted(p.name for p in folder.iterdir()) == [
        *[f"boots-on-gravel_0{n}.wav" for n in range(1, 7)], "set.provenance.json"]
    assert not [p for p in root.iterdir() if p.name.startswith(".")]            # nothing half-made is left
    record = json.loads((folder / "set.provenance.json").read_text())
    source = json.loads((root / SOURCE / "sound.provenance.json").read_text())
    assert record["kind"] == "sound_set" and record["schema_version"] == 1 and record["made_with"]
    assert record["license"] == source["license"]                              # inherited, unchanged
    assert record["output"] == {"files": 6, "sample_rate": 48_000, "channels": 1, "classification": "commercial-clear"}
    assert record["source"] == {"id": SOURCE, "path": f"sounds/{SOURCE}/sound.wav",
                                "sha256": sound_post.sha256_file(root / SOURCE / "sound.wav")}
    assert record["variation"] == {
        "of": SOURCE, "count": 6, "seed": 21, "how_different": "some", "change": ["pitch", "volume"],
        "most": {"pitch_semitones": 2.5, "volume_db": 3.0}, "turned_down_db": record["variation"]["turned_down_db"],
        "pitch_by": "resampling, so a higher take is shorter and a lower one longer"}
    assert record["layers"][0]["source"]["prompt"] == "heavy boots on gravel, one step"
    for n, item in enumerate(record["files"], start=1):
        wav = folder / item["file"]
        facts = sound_post.inspect_wav(wav)                                     # 48 kHz, mono, not silent
        assert item["sha256"] == facts["sha256"] and item["seconds"] == facts["seconds"]
        assert facts["peak"] <= 0.98 + 1e-3
        assert item["variation"]["of"] == SOURCE and item["variation"]["index"] == n
        assert item["variation"]["seed"] == 21
        assert set(item["variation"]) == {"of", "seed", "index", "pitch_semitones", "volume_db", "gain_db",
                                          "start_ms", "note"}
    lengths = {item["seconds"] for item in record["files"]}
    assert len(lengths) > 1                                                     # pitch moved, so lengths did too
    assert saved["licence"] == "Apache-2.0" and saved["seed"] == 21
    assert [f["file"] for f in saved["files"]][0] == f"{saved['folder']}/boots-on-gravel_01.wav"


def test_the_record_rebuilds_the_set(tmp_path):
    root = kept_boots(tmp_path)
    first = api.save_variation_set(root, SOURCE, {"count": 5, "how_different": "a lot", "change": ["pitch"]})
    made = json.loads((tmp_path / first["folder"] / "set.provenance.json").read_text())["variation"]
    again = api.save_variation_set(root, SOURCE, {k: made[k] for k in ("count", "seed", "how_different", "change")})
    assert again["folder"] != first["folder"]                                   # same second: a folder of its own
    read = lambda saved: [(tmp_path / f["file"]).read_bytes() for f in saved["files"]]
    assert read(first) == read(again)


def test_a_set_can_be_given_its_own_name(tmp_path):
    saved = api.save_variation_set(kept_boots(tmp_path), SOURCE, {"count": 2, "name": "Boot steps", "seed": 1})
    assert saved["name"] == "Boot-steps" and saved["folder"].startswith("sounds/Boot-steps_set__")
    assert [Path(f["file"]).name for f in saved["files"]] == ["Boot-steps_01.wav", "Boot-steps_02.wav"]


def test_a_source_with_no_record_makes_an_unverified_set(tmp_path):
    saved = api.save_variation_set(kept_boots(tmp_path, record=False), SOURCE, {"count": 2, "seed": 1})
    record = json.loads((tmp_path / saved["folder"] / "set.provenance.json").read_text())
    assert record["license"]["verified"] is False and record["output"]["classification"] == "unverified"
    assert record["layers"] == [] and record["source_record"] is None


@pytest.mark.parametrize("bad", ["../../etc", "..", "boots", ".tries", "a/b__20261120-091306", "", None, 7,
                                 SOURCE + "\n", "nobody-home__20261120-091306",
                                 "boots-on-gravel_set__20261120-091306"])
def test_only_a_kept_sound_under_output_sounds_can_be_varied(tmp_path, bad):
    root = kept_boots(tmp_path)
    sound_post.write_wav(root / "boots-on-gravel_set__20261120-091306" / "boots-on-gravel_01.wav",
                         np.full(480, 0.5, np.float32))                         # a set is not a source
    for work in (api.variations_preview, api.save_variation_set):
        with pytest.raises(LookupError):
            work(root, bad, {"count": 2})
    assert sorted(p.name for p in root.iterdir()) == [SOURCE, "boots-on-gravel_set__20261120-091306"]


def test_a_link_out_of_the_folder_is_not_a_kept_sound(tmp_path):
    root = kept_boots(tmp_path)
    outside = kept_boots(tmp_path / "elsewhere", name="secret__20261120-091306") / "secret__20261120-091306"
    (root / "secret__20261120-091306").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LookupError):
        api.kept_folder(root, "secret__20261120-091306")


def test_a_bad_request_saves_nothing(tmp_path):
    root = kept_boots(tmp_path)
    with pytest.raises(ValueError, match="between 2 and 12"):
        api.save_variation_set(root, SOURCE, {"count": 40})
    assert [p.name for p in root.iterdir()] == [SOURCE]


def test_the_library_lists_a_set_as_a_set(tmp_path):
    root = kept_boots(tmp_path)
    saved = api.save_variation_set(root, SOURCE, {"count": 3, "seed": 4})
    (tmp_path / saved["folder"] / "notes.txt").write_text("mine")              # a stray is not a sound
    (root / "empty_set__20261120-101500").mkdir()                              # nor is an empty folder a set
    listed = {item["kind"]: item for item in api.kept_sounds(tmp_path)}
    assert set(listed) == {"sound", "sound_set"} and len(api.kept_sounds(tmp_path)) == 2
    made = listed["sound_set"]
    assert made["id"] == saved["id"] and made["name"] == "Boots on gravel" and made["count"] == 3
    assert made["files"] == [f["file"] for f in saved["files"]] and made["file"] == made["files"][0]
    assert made["licence"] == "Apache-2.0" and made["of"] == SOURCE and made["seconds"] == 0.5
    assert made["prompt"] == "heavy boots on gravel, one step"
    assert len(made["play_order"]) == 24 and set(made["play_order"]) == {0, 1, 2}
    (tmp_path / saved["folder"] / "set.provenance.json").write_text("{not json")
    bare = next(item for item in api.kept_sounds(tmp_path) if item["kind"] == "sound_set")
    assert bare["count"] == 3 and bare["licence"] is None                      # still listed; nothing claimed


def test_the_sheet_is_told_what_variations_it_may_ask_for():
    told = api.defaults_payload()["variations"]
    assert (told["min"], told["max"], told["default"], told["default_how_different"]) == (2, 12, 6, "a little")
    assert [(h["id"], h["label"]) for h in told["how_different"]] == [
        ("a little", "A little"), ("some", "Some"), ("a lot", "A lot")]
    assert {h["id"] for h in told["how_different"]} == set(sound_post.HOW_DIFFERENT)
    assert [c["label"] for c in told["change"]] == ["Pitch", "Volume"]
    assert "shorter" in told["change"][0]["hint"]                              # pitch changes the length: say so
    assert all(len(item["hint"]) < 90 for item in told["how_different"] + told["change"])


# --------------------------------------------------------------------------- background loops

LOOP = {"kind": "loop", "prompt": "rain on a tent", "seconds": 8, "seed": 7, "count": 1}


def steady(t):
    """What a good engine answers for a loop: noise that goes on for as long as it was asked."""
    return 0.9 * np.random.default_rng(3).standard_normal(t.size)


def test_a_loop_request_has_its_own_lengths():
    request = api.clean_request(LOOP)
    assert (request["kind"], request["seconds"], request["prompt"]) == ("loop", 8.0, "rain on a tent")
    assert api.clean_request({"kind": "loop", "prompt": "wind"})["seconds"] == 8.0        # its own usual length
    assert api.clean_request({**LOOP, "seconds": 4})["seconds"] == 4.0
    assert api.clean_request({**LOOP, "seconds": 30})["seconds"] == 30.0
    for seconds in (3.9, 1.5, 30.5):
        with pytest.raises(ValueError, match="a background loop is 4 to 30 seconds long"):
            api.clean_request({**LOOP, "seconds": seconds})
    assert api.clean_request({"prompt": "a door"})["seconds"] == 1.5                       # effects are as they were
    with pytest.raises(ValueError, match="Music has its own page"):
        api.clean_request({**LOOP, "kind": "music"})


@pytest.mark.parametrize(("typed", "sent"), [
    ("rain on a tent", "rain on a tent, continuous, steady, no single events"),
    ("  spaceship engine\n hum ", "spaceship engine hum, continuous, steady, no single events"),
    # already says how steady it is: not a word is added
    ("steady rain on a tent", "steady rain on a tent"),
    ("a constant low hum", "a constant low hum"),
    ("Looping wind over a moor", "Looping wind over a moor"),
    ("an engine droning, non-stop", "an engine droning, non-stop"),
    # asks for things to happen: never told "no single events"
    ("a sea storm with occasional thunder", "a sea storm with occasional thunder, continuous"),
    ("forest at night, an owl now and then", "forest at night, an owl now and then, continuous"),
    ("fire with random crackles and bursts", "fire with random crackles and bursts, continuous"),
])
def test_a_loops_description_gets_steadying_words_only_where_it_has_none(typed, sent):
    assert api.loop_prompt(typed) == sent
    assert api.loop_prompt(typed).startswith(" ".join(typed.split()))          # the user's words, whole and first


def test_an_effects_description_is_never_touched():
    assert api.engine_prompt(api.clean_request({"prompt": "a door slams"})) == "a door slams"
    assert api.engine_prompt(api.clean_request(LOOP)) == "rain on a tent, continuous, steady, no single events"


def test_the_engine_is_asked_for_the_loop_and_what_its_join_needs(tmp_path):
    pipe = FakePipe(shape=steady)
    runner.generate(pipe, api.clean_request(LOOP), 7, 1, lambda *a, **k: None, most=api.MOSS.max_seconds)
    (call,) = pipe.calls
    # the words say the whole length made, so the sound does not stop where the loop ends
    assert call["prompt"] == "rain on a tent, continuous, steady, no single events duration: 9.5s"
    assert call["seconds"] == 9.5 and call["suffix"] is False
    pipe = FakePipe(shape=steady)
    runner.generate(pipe, api.clean_request({**LOOP, "seconds": 30}), 7, 1, lambda *a, **k: None, most=30.0)
    assert pipe.calls[0]["seconds"] == 30.0 and pipe.calls[0]["prompt"].endswith(" duration: 30.0s")


def test_a_loop_is_made_levelled_and_written_with_its_loop_inside(tmp_path):
    request = api.clean_request(LOOP)
    facts = runner.make_one(FakePipe(shape=steady), api.MOSS, request, 1, tmp_path, lambda *a, **k: None)
    wav = tmp_path / "sound.wav"
    sound, rate = sound_post.read_wav(wav)
    assert rate == 48_000 and sound.size == 8 * 48_000 and facts["seconds"] == 8.0
    assert sound_post.read_loop(wav) == {"start_sample": 0, "end_sample": sound.size - 1}
    assert sound_post.seam_report(sound)["step_ratio"] < 4.0             # round it goes, no click
    assert abs(sound[0]) > 1e-3 or abs(sound[1]) > 1e-3                  # and no fade at its ends
    assert sound_post.loudness_db(sound) == pytest.approx(-20.0, abs=0.1)   # a wall of noise came in
    assert facts["level_db"] == pytest.approx(-20.0, abs=0.1) and facts["peak"] < 0.6

    record = json.loads((tmp_path / "sound.provenance.json").read_text())
    assert record["sound_kind"] == "loop"
    assert record["loop"] == {"start_sample": 0, "end_sample": sound.size - 1, "crossfade_ms": 800.0}
    assert set(record["loudness"]) == {"target", "measured", "peak"}
    assert record["loudness"]["target"] == -20.0 and record["loudness"]["measured"] == pytest.approx(-20.0, abs=0.1)
    assert record["loudness"]["peak"] == facts["peak"]
    post = record["post"]
    assert (post["fade_ms"], post["fade_out_ms"], post["tail_s"]) == (0.0, 0.0, 0.0) and post["gain_db"] < 0
    assert post["join"]["step_ratio"] < 4.0 and "level_jump_db" in post["join"]
    source = record["layers"][0]["source"]
    assert source["prompt"] == "rain on a tent"                          # what was typed...
    assert source["prompt_sent"] == "rain on a tent, continuous, steady, no single events"   # ...and what was sent
    assert source["seconds_asked"] == 8.0 and record["license"]["name"] == "Apache-2.0"


def test_an_effects_record_is_as_it_was(tmp_path):
    runner.make_one(FakePipe(), api.MOSS, api.clean_request({**ASK, "count": 1}), 1, tmp_path, lambda *a, **k: None)
    record = json.loads((tmp_path / "sound.provenance.json").read_text())
    assert record["loop"] is None and record["loudness"]["target"] is None and "join" not in record["post"]
    assert "prompt_sent" not in record["layers"][0]["source"]
    assert sound_post.read_loop(tmp_path / "sound.wav") is None
    assert sound_post.peak_of(sound_post.read_wav(tmp_path / "sound.wav")[0]) == pytest.approx(0.98, abs=2e-3)


def test_a_loop_that_stops_too_soon_is_reported_not_kept(tmp_path):
    def stops(t):
        return np.where(t < 1.0, 0.5 * np.sin(2 * np.pi * 200 * t), 0.0)
    said = []
    made = runner.make_batch(FakePipe(shape=stops), api.MOSS, api.clean_request(LOOP), tmp_path,
                             lambda event, **f: said.append((event, f)))
    assert made == [] and not (tmp_path / "sound.wav").exists()
    assert [f["error"] for e, f in said if e == "problem"] == ["Try 1: the sound stops too soon to make a loop of it."]


def test_the_command_says_which_kind(tmp_path):
    command = api.build_command(api.clean_request(LOOP), tmp_path, api.MOSS)
    assert command[command.index("--kind") + 1] == "loop" and command[command.index("--seconds") + 1] == "8.0"
    command = api.build_command(api.clean_request(ASK), tmp_path, api.MOSS)
    assert command[command.index("--kind") + 1] == "effect"


def test_the_runner_takes_the_kind_from_its_command_line(tmp_path):
    pipe = FakePipe(shape=steady)
    code = runner.main(["--kind", "loop", "--prompt", "rain on a tent", "--seconds", "4", "--count", "1", "--seed", "3",
                        "--steps", "10", "--model-dir", str(weights_folder(tmp_path)), "--out", str(tmp_path / "out")],
                       load=lambda weights: pipe)
    assert code == 0 and pipe.calls[0]["seconds"] == 5.1
    assert sound_post.read_loop(tmp_path / "out" / "sound.wav")["end_sample"] == 4 * 48_000 - 1
    with pytest.raises(SystemExit, match="a background loop is 4 to 30 seconds long"):
        runner.main(["--kind", "loop", "--prompt", "rain", "--seconds", "2", "--model-dir", str(weights_folder(tmp_path))])


def keep_a_loop(tmp_path):
    manager = api.SoundJobManager(tmp_path / "sounds")
    job = manager.create(api.clean_request(LOOP))
    runner.make_one(FakePipe(shape=steady), api.MOSS, job.request, 1, job.directory, lambda *a, **k: None)
    job.tries.append({"number": 1})
    return manager, manager.keep(job.id, 1)


def test_a_kept_loop_keeps_its_loop_and_the_library_says_it_is_one(tmp_path):
    _, kept = keep_a_loop(tmp_path)
    wav = tmp_path / kept["file"]
    assert sound_post.read_loop(wav) == {"start_sample": 0, "end_sample": 8 * 48_000 - 1}
    (listed,) = api.kept_sounds(tmp_path)
    assert listed["sound_kind"] == "loop" and listed["loop"] is True and listed["seconds"] == 8.0
    assert listed["prompt"] == "rain on a tent"


def test_a_loop_has_no_variations_and_an_effect_still_does(tmp_path):
    _, kept = keep_a_loop(tmp_path)
    loop_id = kept["file"].split("/")[1]
    for ask in (api.variations_preview, api.save_variation_set):
        with pytest.raises(ValueError, match="a background loop has no variations"):
            ask(tmp_path / "sounds", loop_id, {})
    assert [p.name for p in (tmp_path / "sounds").iterdir() if "_set" in p.name] == []
