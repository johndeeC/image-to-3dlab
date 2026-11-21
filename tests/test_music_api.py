"""Tests for viewer/music_api.py and scripts/music_generate.py: music, without a model.

The engine is replaced by a stand-in with the call shape the runner uses (`compose`), and
the engine's own package by a stand-in with the entry points the real one has, so nothing
here needs MLX, PyTorch, weights or the network. Everything else is the shipped code,
imported: the request check, the words-only rule, the length guard, the levelling, the loop,
the record and the job.
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "scripts"), str(REPO / "viewer")]

import music_api as api
import music_generate as runner
import sound_api

from image_to_3dlab import sound_post
from image_to_3dlab.provenance import LICENSES

VILLAGE = "calm fantasy village theme, harp, flute, soft strings"
ENGINE = api.ACE_STEP


def song(seconds, rate=48_000, level=0.3, seed=0):
    """Something shaped like the engine's answer: (2, frames), two channels that differ."""
    t = np.arange(int(seconds * rate)) / rate
    rng = np.random.default_rng(seed)
    left = np.sin(2 * np.pi * (220 + seed) * t) + 0.4 * np.sin(2 * np.pi * 330 * t)
    right = np.sin(2 * np.pi * (220 + seed) * t) + 0.4 * np.sin(2 * np.pi * 277 * t)
    both = np.stack([left, right]) + 0.05 * rng.standard_normal((2, t.size))
    return (level * both / np.abs(both).max()).astype(np.float32)


class FakeComposer:
    """Stands in for `runner.Composer`: the same `compose`, answering 48 kHz stereo."""

    def __init__(self, silent=(), wrong_length=(), mono=False):
        self.calls = []
        self.silent, self.wrong_length, self.mono = set(silent), set(wrong_length), mono

    def compose(self, request, *, seed, seconds, progress=None):
        self.calls.append({"prompt": api.engine_prompt(request), "seed": seed, "seconds": seconds,
                           "plan": request["plan"], "lyrics": request.get("lyrics")})
        if progress:
            progress(0.5)
            progress(0.99)
        length = seconds * 3 if seed in self.wrong_length else seconds
        audio = song(length, level=0.0 if seed in self.silent else 0.3, seed=seed % 5)
        told = {"planned": {"tempo_bpm": 73, "key": "D major", "description": "A gentle piece."}} if request["plan"] else {}
        return (audio[:1] if self.mono else audio), 48_000, told


def weights_folder(tmp_path, leave=()):
    folder = tmp_path / "weights"
    for name in ENGINE.needs:
        if name not in leave:
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(b"code" if name.endswith(".py") else b"x")
    return folder


# ----------------------------------------------------------------- the engine, as documented

def test_the_engine_is_what_its_card_says():
    assert ENGINE.repo == "ACE-Step/Ace-Step1.5" and len(ENGINE.revision) == 40
    assert ENGINE.sample_rate == 48_000 and ENGINE.channels == 2
    assert (ENGINE.min_seconds, ENGINE.max_seconds, ENGINE.default_seconds) == (10.0, 240.0, 60.0)
    assert ENGINE.environment == REPO / "vendor" / "music" and ENGINE.size == "10.1 GB"
    profile = LICENSES[ENGINE.licence_key]
    assert profile.license_name == "MIT" and profile.classification == "commercial-clear"
    said = " ".join(profile.conditions)
    assert "we have not checked that" in said and "licensed music" in said      # the card's claim, as a claim
    assert "No recording was given" in said
    # the four parts, named by what they do, and they are the whole download
    assert [part.label for part in ENGINE.parts] == ["music model", "song planner", "text reader", "sound decoder"]
    assert [part.optional for part in ENGINE.parts] == [False, True, False, False]
    assert round(sum(part.size for part in ENGINE.parts) / 1e9, 1) == 10.1
    for part in ENGINE.parts:
        assert any(name.startswith(part.folder + "/") for name in ENGINE.needs)


def test_a_good_request_comes_back_tidy():
    request = api.clean_request({"prompt": f"  {VILLAGE}  ", "seconds": 30, "seed": 7})
    assert request["prompt"] == VILLAGE and request["kind"] == "music" and request["seconds"] == 30.0
    assert request["singing"] is False and request["lyrics"] is None       # instrumental unless asked
    assert request["plan"] is True and request["loop"] is False
    assert request["count"] == 2 and request["seed"] == 7 and request["steps"] == 8
    assert request["name"] == "calm-fantasy-village-theme-harp-flute-soft-strings"
    sung = api.clean_request({"description": "sea shanty", "lyrics": " Heave ho \n  away we go ", "language": "EN"})
    assert sung["singing"] is True and sung["lyrics"] == "Heave ho\naway we go" and sung["language"] == "en"
    assert sung["seconds"] == 60.0 and 0 <= sung["seed"] <= sound_api.MAX_SEED


@pytest.mark.parametrize("change, why", [
    ({"prompt": ""}, "describe the music first"),
    ({"prompt": "x" * 501}, "longer than 500"),
    ({"seconds": 9}, "from 10 to 240 seconds"),
    ({"seconds": 241}, "from 10 to 240 seconds"),
    ({"seconds": "long"}, "must be a number"),
    ({"count": 5}, "between 1 and 4"),
    ({"seed": -1}, "seed must be between"),
    ({"singing": True}, "write the words to sing first"),
    ({"singing": False, "lyrics": "la la"}, "singing is off"),
    ({"lyrics": "la " * 1001}, "longer than 3000"),
    ({"lyrics": "la", "language": "klingon"}, "no singing language"),
    ({"loop": True, "lyrics": "la la"}, "a loop has no singing"),
    ({"plan": "yes"}, "on or off"),
])
def test_anything_past_a_limit_is_refused_not_clipped(change, why):
    with pytest.raises(ValueError, match=why):
        api.clean_request({"prompt": VILLAGE, **change})


@pytest.mark.parametrize("key", api.REFERENCE_KEYS)
def test_a_recording_to_copy_is_refused_by_name(key):
    with pytest.raises(ValueError, match="words only"):
        api.clean_request({"prompt": VILLAGE, key: "their_song.mp3"})


def test_a_loop_gets_steadying_words_unless_it_already_asks_for_a_loop():
    loop = api.clean_request({"prompt": "tense battle music", "loop": True})
    assert api.engine_prompt(loop) == "tense battle music, seamless loop, steady, no intro, no ending"
    assert api.engine_prompt(api.clean_request({"prompt": "tense battle loop", "loop": True})) == "tense battle loop"
    assert api.engine_prompt(api.clean_request({"prompt": "tense battle music"})) == "tense battle music"


def test_with_nothing_installed_it_says_what_is_missing(tmp_path):
    engine = api.MusicEngine(**{**ENGINE.__dict__, "environment": tmp_path / "none"})
    assert api.missing(engine, tmp_path / "hub") == ["its own Python environment", "its model files (10.1 GB)"]
    with pytest.raises(sound_api.MissingSound, match="ACE-Step 1.5 is not set up yet"):
        api.check_ready(engine, tmp_path / "hub")


# ----------------------------------------------------------------- the runner

def test_the_runner_refuses_before_loading_when_weights_are_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-hub"))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    loaded = []
    with pytest.raises(SystemExit) as stop:
        runner.main(["--description", VILLAGE, "--out", str(tmp_path / "out")], load=lambda *a: loaded.append(a))
    said = str(stop.value)
    assert "ACE-Step 1.5" in said and "MLX and PyTorch" in said and "10.1 GB" in said
    assert "does not fetch" in said and loaded == [] and not (tmp_path / "out").exists()


def test_the_runner_refuses_a_half_finished_download_and_pins_itself_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    half = weights_folder(tmp_path, leave=("vae/diffusion_pytorch_model.safetensors",))
    with pytest.raises(SystemExit, match="not finished. Missing: vae/diffusion_pytorch_model.safetensors"):
        runner.local_weights(ENGINE, str(half))
    (half / "vae" / "diffusion_pytorch_model.safetensors").write_bytes(b"x")
    assert runner.local_weights(ENGINE, str(half)) == half and os.environ["HF_HUB_OFFLINE"] == "1"


def test_the_runner_refuses_a_request_past_the_limits_before_anything_else():
    for argv, why in ((["--description", VILLAGE, "--seconds", "300"], "from 10 to 240"),
                      (["--description", VILLAGE, "--count", "9"], "between 1 and 4"),
                      (["--description", VILLAGE, "--loop", "--lyrics", "la la"], "a loop has no singing")):
        with pytest.raises(SystemExit, match=why):
            runner.main(argv, load=lambda *a: pytest.fail("loaded the model"))


def test_the_planner_switch_reaches_the_loader_and_the_engine(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    weights = weights_folder(tmp_path)
    for flag, plan in (("--plan", True), ("--no-plan", False), (None, True)):
        loads, composer = [], FakeComposer()
        argv = ["--description", VILLAGE, "--seconds", "10", "--count", "1", "--seed", "3",
                "--model-dir", str(weights), "--out", str(tmp_path / f"out-{flag}")] + ([flag] if flag else [])
        assert runner.main(argv, load=lambda *a, loads=loads, composer=composer: (loads.append(a), composer)[1]) == 0
        assert loads == [(ENGINE, weights, plan)] and composer.calls[0]["plan"] is plan
        record = json.loads((tmp_path / f"out-{flag}" / "sound.provenance.json").read_text())
        assert record["music"]["planner_used"] is plan
        parts = [part["part"] for part in record["music"]["parts_used"]]
        assert ("song planner" in parts) is plan and parts[0] == "music model"
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert [e["event"] for e in events[:5]] == ["loading", "step", "step", "try", "done"]
    assert events[1] == {"event": "step", "number": 1, "step": 50, "total": 100}


def test_a_tune_is_made_stereo_levelled_checked_and_written_with_its_record(tmp_path):
    composer = FakeComposer()
    request = api.clean_request({"prompt": VILLAGE, "seconds": 12, "seed": 7, "count": 1})
    facts = runner.make_one(composer, ENGINE, request, 1, tmp_path, tell=lambda *a, **k: None)

    assert composer.calls == [{"prompt": VILLAGE, "seed": 7, "seconds": 12.0, "plan": True, "lyrics": None}]
    wav = tmp_path / "sound.wav"
    made = sound_post.inspect_wav(wav, want_channels=2)
    assert made["channels"] == 2 and made["sample_rate"] == 48_000 and made["seconds"] == pytest.approx(12.0, abs=0.05)
    audio, _ = sound_post.read_wav(wav)
    assert sound_post.loudness_db(audio) == pytest.approx(-18.0, abs=0.6) and sound_post.peak_of(audio) <= 0.981
    assert facts["seed"] == 7 and facts["level_db"] == pytest.approx(-18.0, abs=0.6) and len(facts["bars"]) == 60
    assert facts["planned"]["tempo_bpm"] == 73

    record = json.loads((tmp_path / "sound.provenance.json").read_text())
    assert record["kind"] == "sound" and record["sound_kind"] == "music" and record["schema_version"] == 1
    assert record["output"]["channels"] == 2 and record["output"]["sha256"] == made["sha256"]
    assert record["license"]["name"] == "MIT" and record["license"]["classification"] == "commercial-clear"
    music = record["music"]
    assert music["description"] == VILLAGE and music["instrumental"] is True and music["lyrics"] is None
    assert music["seed"] == 7 and music["seconds_asked"] == 12.0 and music["stereo"] is True
    assert music["planner_used"] is True and music["planned"]["key"] == "D major"
    assert music["reference_audio"] is None and music["made_from"] == "words only"
    assert music["model"] == "ACE-Step/Ace-Step1.5" and music["revision"] == ENGINE.revision
    assert [part["part"] for part in music["parts_used"]] == ["music model", "song planner", "text reader", "sound decoder"]
    source = record["layers"][0]["source"]
    assert source["seed"] == 7 and source["steps"] == 8 and "ca1e85fe" in source["runtime"]
    assert record["loudness"]["target"] == -18.0 and record["loop"] is None
    assert record["post"]["ended"].startswith("faded out")           # the stand-in never ends by itself


def test_sung_music_records_its_words(tmp_path):
    request = api.clean_request({"prompt": "sea shanty", "lyrics": "Heave ho\naway we go", "language": "en",
                                 "seconds": 10, "seed": 1, "count": 1, "plan": False})
    composer = FakeComposer()
    runner.make_one(composer, ENGINE, request, 1, tmp_path, tell=lambda *a, **k: None)
    assert composer.calls[0]["lyrics"] == "Heave ho\naway we go"
    music = json.loads((tmp_path / "sound.provenance.json").read_text())["music"]
    assert music["instrumental"] is False and music["lyrics"] == "Heave ho\naway we go" and music["language"] == "en"
    assert music["planned"] is None and music["planner_used"] is False


def test_a_loop_asks_for_more_and_comes_out_joined_with_its_loop_in_the_file(tmp_path):
    composer = FakeComposer()
    request = api.clean_request({"prompt": "tense battle music", "seconds": 20, "loop": True, "seed": 4, "count": 1})
    facts = runner.make_one(composer, ENGINE, request, 1, tmp_path, tell=lambda *a, **k: None)
    assert composer.calls[0]["seconds"] == 23.5 and composer.calls[0]["prompt"].endswith("no intro, no ending")
    wav = tmp_path / "sound.wav"
    loop = sound_post.read_loop(wav)
    audio, _ = sound_post.read_wav(wav)
    assert loop == {"start_sample": 0, "end_sample": audio.shape[0] - 1} and audio.shape[1] == 2
    assert facts["seconds"] == pytest.approx(20.0, abs=0.51)
    record = json.loads((tmp_path / "sound.provenance.json").read_text())
    assert record["loop"]["crossfade_ms"] == 1000.0 and record["music"]["loops"] is True
    assert record["post"]["join"]["step_ratio"] < 4.0 and record["post"]["fade_out_ms"] == 0.0
    assert record["layers"][0]["source"]["prompt_sent"].endswith("no intro, no ending")
    assert sound_post.seam_report_frames(audio)["step_ratio"] < 4.0        # 16 bits do not open the join


def test_a_try_of_the_wrong_length_or_silent_is_dropped_and_the_rest_are_made(tmp_path):
    said = []
    request = api.clean_request({"prompt": VILLAGE, "seconds": 10, "seed": 10, "count": 4})
    made = runner.make_batch(FakeComposer(wrong_length={11}, silent={12}), ENGINE, request, tmp_path,
                             tell=lambda event, **fields: said.append((event, fields)))
    assert [item["number"] for item in made] == [1, 4] and [item["seed"] for item in made] == [10, 13]
    problems = [fields["error"] for event, fields in said if event == "problem"]
    assert "Try 2: it came out 30 s long, not the 10 s asked, so it was dropped." in problems
    assert any(text.startswith("Try 3:") and "silent" in text for text in problems)
    assert (tmp_path / "try-1" / "sound.wav").is_file() and not (tmp_path / "try-2" / "sound.wav").exists()


def test_music_that_is_not_stereo_is_refused(tmp_path):
    request = api.clean_request({"prompt": VILLAGE, "seconds": 10, "seed": 1, "count": 1})
    with pytest.raises(sound_post.SoundProblem, match="1 channel, not 2"):
        runner.make_one(FakeComposer(mono=True), ENGINE, request, 1, tmp_path, tell=lambda *a, **k: None)


def test_the_engine_reads_links_and_only_ever_writes_to_copies(tmp_path):
    weights = weights_folder(tmp_path)
    view = runner.checkpoint_view(weights, ENGINE, tmp_path / "home")
    assert view == tmp_path / "home" / "checkpoints"
    for name in ENGINE.needs:
        target = view / name
        assert target.is_symlink() is (not name.endswith(".py")), name
        assert target.read_bytes() == (weights / name).read_bytes()
    code = view / "acestep-v15-turbo" / "modeling_acestep_v15_turbo.py"
    code.write_bytes(b"the engine's own copy")                      # what the engine does on start
    assert (weights / "acestep-v15-turbo" / "modeling_acestep_v15_turbo.py").read_bytes() == b"code"
    # a second run, with the weights moved: links follow, nothing is left pointing at the old place
    moved = tmp_path / "moved"
    weights.rename(moved)
    view = runner.checkpoint_view(moved, ENGINE, tmp_path / "home")
    assert (view / "vae" / "config.json").resolve() == (moved / "vae" / "config.json").resolve()
    assert code.read_bytes() == b"code"


def fake_engine_package(monkeypatch, have_models=True, starts=True):
    """The entry points of the real `acestep` package that the loader touches."""
    seen = {"init": [], "planner": [], "generate": []}

    class Handler:
        def initialize_service(self, **kwargs):
            seen["init"].append(kwargs)
            return ("ok" if starts else "ERROR: boom\ntraceback"), starts

    class Planner:
        llm_initialized = False

        def initialize(self, **kwargs):
            seen["planner"].append(kwargs)
            return "ok", True

    class Params:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def generate_music(handler, planner, params, config, save_dir=None, progress=None):
        seen["generate"].append((params, config, save_dir))
        progress(0.6, desc="Generating music...")
        tensor = types.SimpleNamespace(detach=lambda: tensor, cpu=lambda: tensor, numpy=lambda: song(params.duration))
        return types.SimpleNamespace(success=True, error=None, audios=[{"tensor": tensor, "sample_rate": 48_000}],
                                     extra_outputs={"lm_metadata": {"bpm": 73, "keyscale": "D major", "caption": "Gentle.",
                                                                    "timesignature": "4", "duration": 30}})

    inference = types.SimpleNamespace(GenerationParams=Params, GenerationConfig=Params, generate_music=generate_music)
    downloader = types.SimpleNamespace(check_main_model_exists=lambda view: have_models,
                                       check_model_exists=lambda name, view: have_models)
    package = types.ModuleType("acestep")
    package.inference, package.model_downloader = inference, downloader
    core = types.SimpleNamespace(set_cache_limit=lambda limit: seen.update(spare=limit))
    mlx = types.ModuleType("mlx")
    mlx.core = core
    modules = {"mlx": mlx, "mlx.core": core, "acestep": package, "acestep.inference": inference, "acestep.model_downloader": downloader,
               "acestep.handler": types.SimpleNamespace(AceStepHandler=Handler),
               "acestep.llm_inference": types.SimpleNamespace(LLMHandler=Planner),
               "loguru": types.SimpleNamespace(logger=types.SimpleNamespace(remove=lambda: None, add=lambda *a, **k: None))}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(runner, "seed_everything", lambda seed: seen.setdefault("seeds", []).append(seed))
    for name in ("ACESTEP_PROJECT_ROOT", "HF_MODULES_CACHE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    return seen


def test_the_loader_starts_the_planner_only_when_asked_and_gives_the_engine_words_only(tmp_path, monkeypatch):
    seen = fake_engine_package(monkeypatch)
    weights, home = weights_folder(tmp_path), tmp_path / "home"
    composer = runner.load_composer(ENGINE, weights, False, root=home)
    assert seen["planner"] == []                                             # 3.8 GB never loaded
    assert seen["init"] == [{"project_root": str(home), "config_path": "acestep-v15-turbo", "device": "auto"}]
    assert os.environ["ACESTEP_PROJECT_ROOT"] == str(home)
    assert seen["spare"] == 2 * 1024**3                                      # MLX's spare memory is capped

    request = api.clean_request({"prompt": VILLAGE, "seconds": 10, "seed": 5, "plan": False})
    steps = []
    audio, rate, told = composer.compose(request, seed=5, seconds=10.0, progress=steps.append)
    params, config, save_dir = seen["generate"][0]
    assert audio.shape == (2, 480_000) and rate == 48_000 and steps == [0.6] and save_dir is None
    assert params.task_type == "text2music" and params.caption == VILLAGE and params.duration == 10.0
    assert params.lyrics == "[Instrumental]" and params.instrumental is True
    assert params.thinking is False and params.use_cot_caption is False and params.seed == 5
    assert params.enable_normalization is False                              # the volume is set by us
    assert not hasattr(params, "reference_audio") and not hasattr(params, "src_audio")
    assert config.batch_size == 1 and config.seeds == [5] and config.use_random_seed is False
    assert seen["seeds"] == [5]              # the planner draws from the same seed, so a try can be made again

    runner.load_composer(ENGINE, weights, True, root=home)
    assert seen["planner"] == [{"checkpoint_dir": str(home / "checkpoints"), "lm_model_path": "acestep-5Hz-lm-1.7B",
                                "backend": "mlx", "device": "auto"}]
    assert told["planned"] == {"tempo_bpm": 73, "key": "D major", "beats_in_a_bar": "4", "description": "Gentle."}
    assert runner.planned_facts({"bpm": 90, "language": "unknown", "keyscale": "N/A"}) == {"tempo_bpm": 90}
    assert runner.planned_facts({}) is None and runner.planned_facts(None) is None


def test_the_loader_stops_rather_than_let_the_engine_download_or_limp(tmp_path, monkeypatch):
    weights = weights_folder(tmp_path)
    seen = fake_engine_package(monkeypatch, have_models=False)
    with pytest.raises(SystemExit, match="Generation does not fetch them"):
        runner.load_composer(ENGINE, weights, False, root=tmp_path / "home")
    assert seen["init"] == []                                                # never reached its downloader
    fake_engine_package(monkeypatch, starts=False)
    with pytest.raises(SystemExit, match="ACE-Step 1.5 did not start: ERROR: boom$"):
        runner.load_composer(ENGINE, weights, False, root=tmp_path / "home")


# ----------------------------------------------------------------- the job

def test_the_command_runs_the_runner_in_the_music_environment(tmp_path):
    request = api.clean_request({"prompt": VILLAGE, "seconds": 30, "seed": 9, "count": 2, "plan": False})
    command = api.build_command(request, tmp_path, ENGINE, tmp_path / "weights")
    assert command[0] == str(ENGINE.python) and command[1] == str(REPO / "scripts" / "music_generate.py")
    assert command[2:] == ["--engine", "acestep", "--description", VILLAGE, "--seconds", "30.0", "--seed", "9",
                           "--count", "2", "--no-plan", "--out", str(tmp_path), "--model-dir", str(tmp_path / "weights")]
    sung = api.clean_request({"prompt": "shanty", "lyrics": "Heave ho", "language": "en", "seed": 1})
    assert api.build_command(sung, tmp_path)[-5:] == ["--out", str(tmp_path), "--lyrics", "Heave ho", "--language", "en"][-5:]
    loop = api.clean_request({"prompt": "battle", "loop": True, "seed": 1})
    assert api.build_command(loop, tmp_path)[-1] == "--loop" and "--plan" in api.build_command(loop, tmp_path)
    # and the runner reads its own command back into the same request
    parsed = {}
    runner.main(command[2:-2] + ["--model-dir", str(weights_folder(tmp_path))],
                load=lambda engine, weights, plan: parsed.update(plan=plan) or FakeComposer())
    assert parsed == {"plan": False} and (tmp_path / "try-2" / "sound.wav").is_file()


def test_a_music_job_is_a_sound_job_with_its_own_facts(tmp_path, monkeypatch):
    manager = sound_api.SoundJobManager(tmp_path / "sounds")
    monkeypatch.setattr(sound_api, "MANAGER", manager)
    monkeypatch.setattr(api, "missing", lambda engine=ENGINE, hub=None: [])
    ran = []
    monkeypatch.setattr(sound_api, "run_job", lambda job, manager: ran.append(job))
    job = api.start({"prompt": VILLAGE, "seconds": 30, "plan": False})
    for _ in range(100):
        if ran:
            break
        __import__("time").sleep(0.01)
    told = job.describe()
    assert told["kind"] == "music" and told["count"] == 2 and told["seconds"] == 30.0
    assert told["music"] == {"engine": "acestep", "singing": False, "lyrics": None, "loop": False, "plan": False,
                             "seconds": 30.0}
    assert job.prepare(job)[1].endswith("music_generate.py")


def test_a_missing_engine_asks_for_setup_and_starts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "missing", lambda engine=ENGINE, hub=None: ["its model files (10.1 GB)"])
    job = types.SimpleNamespace(engine=ENGINE, request={}, directory=tmp_path)
    with pytest.raises(sound_api.MissingSound, match="Open Setup"):
        api.prepare(job)


def test_the_sheet_is_told_the_limits_the_switches_and_the_parts(monkeypatch):
    monkeypatch.setattr(api, "missing", lambda engine=ENGINE, hub=None: [])
    told = api.defaults_payload()
    assert told["installed"] is True and told["download_size"] == "10.1 GB" and told["setup_id"] == "music"
    assert told["limits"] == {"min_seconds": 10.0, "max_seconds": 240.0, "default_seconds": 60.0, "max_prompt": 500,
                              "max_lyrics": 3000, "tries": 2, "max_tries": 4}
    assert told["plan"]["label"] == "Plan the song first" and told["plan"]["default"] is True
    assert told["singing"]["default"] is False and told["loop"]["default"] is False and told["stereo"] is True
    assert [(part["label"], part["gb"]) for part in told["parts"]] == [
        ("music model", 4.79), ("song planner", 3.76), ("text reader", 1.21), ("sound decoder", 0.34)]
    words = json.dumps(told)
    for jargon in ("DiT", "LM", "CFG", "BPM", "VAE", "diffusion"):
        assert jargon not in words, jargon
