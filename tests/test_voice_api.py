"""Tests for viewer/voice_api.py and scripts/voice_generate.py: voice lines, without a model.

The engines are replaced by stand-ins with mlx-audio's own call shapes (`generate` for
Kokoro, `generate_custom_voice` for Qwen3-TTS), so nothing here needs MLX, weights or the
network. Everything else is the shipped code, imported: the request check, the consent rule,
the length guard, the levelling, the record, the job and the join.
"""

from __future__ import annotations

import json
import sys
import textwrap
import types
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "scripts"), str(REPO / "viewer")]

import sound_api
import voice_api as api
import voice_generate as runner

from image_to_3dlab import sound_post, voice_text
from image_to_3dlab.provenance import LICENSES

HALT = "Halt! Who goes there?"
SHOP = "Welcome, traveller! Take a look at my wares. The potions are fresh today."


def talk(seconds, rate=24_000, level=0.2, hz=160.0):
    """Something shaped like a spoken line: a tone in bursts, with dead air at both ends."""
    t = np.arange(int(seconds * rate)) / rate
    wave = level * np.sin(2 * np.pi * hz * t) * (np.sin(2 * np.pi * 2.0 * t) > -0.3)
    pad = np.zeros(rate // 10, np.float32)
    return np.concatenate([pad, wave.astype(np.float32), pad])


class FakeResult:
    def __init__(self, audio, tokens):
        self.audio, self.token_count = audio, tokens


class FakeQwen:
    """Stands in for mlx-audio's Qwen3-TTS model: `generate_custom_voice`, 24 kHz, 12.5 tokens a second."""

    sample_rate = 24_000

    def __init__(self, runs_on=(), silent=()):
        self.calls, self.seeds = [], []
        self.runs_on, self.silent = set(runs_on), set(silent)

    def seed(self, value):
        self.seeds.append(value)

    def generate_custom_voice(self, text, speaker, language="auto", instruct=None, max_tokens=None, **more):
        assert not more, f"only the preset-voice arguments are passed, not {sorted(more)}"
        self.calls.append({"text": text, "speaker": speaker, "language": language, "instruct": instruct,
                           "max_tokens": max_tokens})
        seed = self.seeds[-1] if self.seeds else 0
        if seed in self.runs_on:                       # stuck in a loop: it writes until it is stopped
            yield FakeResult(talk(max_tokens / 12.5), max_tokens)
            return
        seconds = 0.4 * len(text.split()) + 0.1 * (seed % 3)
        audio = talk(seconds, level=0.0 if seed in self.silent else 0.05 + 0.1 * (seed % 2))
        yield FakeResult(audio, int(seconds * 12.5))

    def generate(self, *args, **kwargs):
        pytest.fail("the cloning entry point was called")


class FakeKokoro:
    """Stands in for mlx-audio's Kokoro model: `generate`, and a pipeline whose word reader
    falls back to whatever stands where espeak would."""

    sample_rate = 24_000

    def __init__(self, unknown=()):
        self.calls, self.unknown = [], set(unknown)
        self.pipelines = {}

    def _get_pipeline(self, code):
        if code not in self.pipelines:
            fallback = sys.modules["misaki.espeak"].EspeakFallback(british=code == "b")
            self.pipelines[code] = types.SimpleNamespace(g2p=types.SimpleNamespace(fallback=fallback))
        return self.pipelines[code]

    def generate(self, text, voice=None, speed=1.0, lang_code="a", **more):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang_code": lang_code})
        fallback = self._get_pipeline(lang_code).g2p.fallback
        for word in text.replace(",", " ").replace(".", " ").split():
            if word in self.unknown:
                fallback(types.SimpleNamespace(text=word))
        yield FakeResult(talk(0.4 * len(text.split()) / speed), 0)


def weights_folder(tmp_path, engine, leave=()):
    folder = tmp_path / f"weights-{engine.id}"
    for name in engine.needs:
        if name not in leave:
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(b"x")
    return folder


def qwen_speaker(**kwargs):
    model = FakeQwen(**kwargs)
    return runner.QwenSpeaker(model, model.seed), model


# ----------------------------------------------------------------- the engines, as documented

def test_the_engines_are_what_their_docs_say():
    kokoro, qwen = api.KOKORO, api.QWEN
    assert (kokoro.repo, kokoro.revision) == ("mlx-community/Kokoro-82M-bf16", "a71e4d38b236d968966a2002c4c895dbd12b1c3c")
    assert (qwen.repo, qwen.revision) == ("mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
                                          "41d3337e8b7f2843a75841595fc14e4b9a7a4b96")
    for engine in (kokoro, qwen):
        profile = LICENSES[engine.licence_key]
        assert profile.license_name == "Apache-2.0" and profile.classification == "commercial-clear"
        assert engine.repo in profile.models and any("preset" in term for term in profile.conditions)
        assert engine.sample_rate == 24_000 and "vendor/voice" in engine.python.as_posix()
        assert engine.default_voice in {voice.id for voice in engine.voices}
        assert all(voice.hint and voice.label for voice in engine.voices)
    assert (kokoro.choice, qwen.choice) == ("Clear and quick", "With feeling")
    # Kokoro: nothing random and nothing to direct; its English voices only (no GPL phonemiser)
    assert (kokoro.seeded, kokoro.directions, kokoro.max_takes) == (False, False, 1)
    assert len(kokoro.voices) == 28 and {voice.id[0] for voice in kokoro.voices} == {"a", "b"}
    assert {name for name, _ in kokoro.packages} == {"misaki", "en_core_web_sm"}
    assert all(f"voices/{voice.id}.safetensors" in kokoro.needs for voice in kokoro.voices)
    # Qwen3-TTS: the nine speakers and ten languages in its config, seeded, directed
    assert [voice.id for voice in qwen.voices] == ["ryan", "aiden", "vivian", "serena", "uncle_fu", "dylan",
                                                  "eric", "ono_anna", "sohee"]
    assert len(qwen.languages) == 10 and (qwen.seeded, qwen.directions, qwen.max_takes) == (True, True, 4)
    assert qwen.tokens_per_second == 12.5 and kokoro.tokens_per_second is None
    assert api.pick_engine() is kokoro and api.pick_engine("qwen") is qwen
    with pytest.raises(ValueError, match="no voice engine 'bark'; choose kokoro or qwen"):
        api.pick_engine("bark")


def test_every_direction_has_a_level_and_plain_words_for_the_engine():
    assert [item["id"] for item in api.DIRECTIONS] == ["angry", "sad", "whisper", "shout", "calm", "excited"]
    assert {item["id"] for item in api.DIRECTIONS} == set(sound_post.VOICE_DIRECTION_DB)
    assert all(item["say"].endswith(".") and item["label"] for item in api.DIRECTIONS)


# ----------------------------------------------------------------- the request

def test_a_good_request_comes_back_tidy():
    asked = api.clean_request({"text": "  Mr. Zog owes me $5.  Pay up, now! ", "voice": "Ryan",
                               "direction": "Shout", "seed": 7, "count": 2}, api.QWEN)
    assert asked["lines"] == ["Mr. Zog owes me $5.", "Pay up, now!"]
    assert asked["spoken"] == ["Mister Zog owes me five dollars.", "Pay up, now!"]
    assert asked["text"] == asked["prompt"] == "Mr. Zog owes me $5. Pay up, now!"
    assert (asked["voice"], asked["language"], asked["speed"]) == ("ryan", "english", 1.0)
    assert asked["direction"] == {"id": "shout", "words": "Shout loudly, with full force."}
    assert (asked["takes"], asked["count"], asked["seed"]) == (2, 4, 7)          # two lines, two takes each
    assert asked["kind"] == "voice" and asked["name"] == "Mr-Zog-owes-me-5-Pay-up-now"
    assert asked["voice_facts"]["engine_label"] == "With feeling" and asked["voice_facts"]["voice_label"] == "Ryan"


def test_defaults_follow_the_engine_and_the_voice():
    kokoro = api.clean_request({"text": HALT, "voice": "bf_emma"}, api.KOKORO)
    assert (kokoro["language"], kokoro["takes"], kokoro["count"], kokoro["direction"]) == ("en-gb", 1, 1, None)
    assert kokoro["lines"] == [HALT]                                   # a short sentence stays with its neighbour
    qwen = api.clean_request({"text": "こんにちは。", "voice": "ono_anna"}, api.QWEN)
    assert qwen["language"] == "japanese" and qwen["takes"] == 4 and 0 <= qwen["seed"] <= api.MAX_SEED
    assert qwen["spoken"] == ["こんにちは。"]
    own = api.clean_request({"text": HALT, "direction": "  like a tired   old wizard "}, api.QWEN)
    assert own["direction"] == {"id": None, "words": "like a tired old wizard"}
    given = api.clean_request({"lines": ["One. Two.", " ", "Three"], "text": "ignored"}, api.QWEN)
    assert given["lines"] == ["One. Two.", "Three"]                     # lines are taken as given


@pytest.mark.parametrize("engine,change,why", [
    (api.QWEN, {"text": ""}, "type the line to say first"),
    (api.QWEN, {"text": "x" * 2401}, "longer than 2400 characters"),
    (api.QWEN, {"text": " ".join(["This is one more sentence."] * 9)}, "9 sentences; make up to 8"),
    (api.QWEN, {"text": "word " * 70}, "a sentence is longer than 300 characters"),
    (api.QWEN, {"voice": "af_heart"}, "With feeling has no voice 'af_heart'"),
    (api.QWEN, {"voice": "/tmp/me.wav"}, "does not copy a person's voice"),
    (api.QWEN, {"language": "klingon"}, "With feeling speaks: english, chinese"),
    (api.QWEN, {"speed": 1.2}, "With feeling has no speed setting"),
    (api.QWEN, {"count": 5}, "between 1 and 4 tries"),
    (api.QWEN, {"count": 0}, "between 1 and 4 tries"),
    (api.QWEN, {"seed": -1}, "seed must be between"),
    (api.QWEN, {"seed": 2**31}, "seed must be between"),
    (api.QWEN, {"direction": "x" * 201}, "under 200 characters"),
    (api.KOKORO, {"direction": "angry"}, "Clear and quick cannot be told how to say a line. With feeling can"),
    (api.KOKORO, {"count": 4}, "the same way every time, so it makes one try"),
    (api.KOKORO, {"speed": 3}, "speed must be between 0.7 and 1.4"),
    (api.KOKORO, {"voice": "af_heart,af_bella"}, "Clear and quick has no voice"),
    (api.KOKORO, {"voice": "ef_dora"}, "Clear and quick has no voice"),
    (api.KOKORO, {"voice": "bf_emma", "language": "en-us"}, "Emma speaks English, British"),
])
def test_anything_past_a_limit_is_refused_not_clipped(engine, change, why):
    with pytest.raises(ValueError, match=why):
        api.clean_request({"text": HALT, **change}, engine)


@pytest.mark.parametrize("key", api.CLONE_KEYS)
def test_a_recording_to_copy_is_refused_by_name(key):
    for engine in (api.KOKORO, api.QWEN):
        with pytest.raises(ValueError, match="does not copy a person's voice"):
            api.clean_request({"text": HALT, key: "me.wav"}, engine)


def test_tries_count_through_the_lines():
    assert [api.line_of(n, 4) for n in (1, 4, 5, 12)] == [(1, 1), (1, 4), (2, 1), (3, 4)]
    assert [api.line_of(n, 1) for n in (1, 2, 3)] == [(1, 1), (2, 1), (3, 1)]
    assert api.try_number(3, 2, 4) == 10 and api.line_of(10, 4) == (3, 2)


# ----------------------------------------------------------------- installed or not

def test_with_nothing_installed_it_says_what_is_missing(tmp_path):
    engine = api.QWEN.__class__(**{**api.QWEN.__dict__, "environment": tmp_path / "voice"})
    absent = api.missing(engine, hub=tmp_path / "hub")
    assert absent == ["its own Python environment", "its model files (3.08 GB)"]
    with pytest.raises(sound_api.MissingSound, match="Qwen3-TTS 1.7B CustomVoice is not set up yet.*Open Setup"):
        api.check_ready(engine, hub=tmp_path / "hub")


def test_kokoro_is_not_installed_until_its_word_reader_is_there(tmp_path, monkeypatch):
    home = tmp_path / "voice"
    engine = api.KOKORO.__class__(**{**api.KOKORO.__dict__, "environment": home})
    python = engine.python
    python.parent.mkdir(parents=True)
    python.write_text("")
    monkeypatch.setattr(sound_api.weight_gate, "snapshot_dir", lambda repo, hub=None: weights_folder(tmp_path, engine))
    assert api.missing(engine) == ["the package that turns English words into sounds",
                                   "spaCy's small English model (13 MB), which reads the sentence first"]
    site = home / ".venv" / "lib" / "python3.11" / "site-packages"
    (site / "misaki").mkdir(parents=True)
    assert api.missing(engine) == ["spaCy's small English model (13 MB), which reads the sentence first"]
    (site / "en_core_web_sm").mkdir()
    assert api.missing(engine) == [] and api.is_installed(engine)


# ----------------------------------------------------------------- the runner

def test_the_runner_refuses_before_loading_when_weights_are_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-hub"))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    loaded = []
    with pytest.raises(SystemExit) as stop:
        runner.main(["--engine", "qwen", "--text", HALT, "--out", str(tmp_path / "out")],
                    load=lambda *a: loaded.append(a))
    said = str(stop.value)
    assert "Qwen3-TTS 1.7B CustomVoice" in said and "8-bit MLX" in said and "3.08 GB" in said
    assert "does not fetch" in said and loaded == [] and not (tmp_path / "out").exists()


def test_the_runner_refuses_a_half_finished_download_and_pins_itself_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    half = weights_folder(tmp_path, api.QWEN, leave=("speech_tokenizer/model.safetensors",))
    with pytest.raises(SystemExit, match="not finished. Missing: speech_tokenizer/model.safetensors"):
        runner.local_weights(api.QWEN, str(half))
    import os
    whole = weights_folder(tmp_path, api.KOKORO)
    assert runner.local_weights(api.KOKORO, str(whole)) == whole and os.environ["HF_HUB_OFFLINE"] == "1"


def test_kokoro_stops_rather_than_let_its_word_reader_fetch_a_model(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    weights = weights_folder(tmp_path, api.KOKORO)
    with pytest.raises(SystemExit) as stop:
        runner.main(["--text", HALT, "--model-dir", str(weights), "--out", str(tmp_path / "out")],
                    load=lambda *a: pytest.fail("loaded the model"),
                    find=lambda name: None if name == "en_core_web_sm" else object())
    said = str(stop.value)
    assert "spaCy's small English model (13 MB)" in said and "does not fetch" in said
    assert "bootstrap_voice.py --engine kokoro" in said
    runner.require_packages(api.QWEN, find=lambda name: None)             # Qwen needs none of it


def test_the_runner_refuses_a_request_past_the_limits_before_anything_else():
    for argv, why in ((["--engine", "kokoro", "--text", HALT, "--direction", "angry"], "cannot be told how"),
                      (["--engine", "qwen", "--text", HALT, "--voice", "me.wav"], "does not copy a person's voice"),
                      (["--engine", "qwen", "--text", HALT, "--count", "9"], "between 1 and 4")):
        with pytest.raises(SystemExit, match=why):
            runner.main(argv, load=lambda *a: pytest.fail("loaded the model"))


def test_a_line_is_said_levelled_checked_and_written_with_its_record(tmp_path):
    speaker, model = qwen_speaker()
    request = api.clean_request({"text": "Dr. Zog owes me $5!", "voice": "ryan", "direction": "whisper",
                                 "seed": 7, "count": 1}, api.QWEN)
    facts = runner.make_one(speaker, api.QWEN, request, 1, tmp_path)

    # the engine got the spoken words, the preset speaker, plain-word direction and a longest length
    assert model.calls == [{"text": "Doctor Zog owes me five dollars!", "speaker": "ryan", "language": "english",
                            "instruct": "Whisper very quietly, as if sharing a secret.",
                            "max_tokens": voice_text.speech_tokens("Doctor Zog owes me five dollars!", 12.5)}]
    assert model.seeds == [7] and model.calls[0]["max_tokens"] == 113         # 9 s, not the model's own 8192
    wav = tmp_path / "sound.wav"
    made = sound_post.inspect_wav(wav)
    assert (made["sample_rate"], made["channels"]) == (48_000, 1) and made["seconds"] < 2.6   # dead air gone
    audio, _ = sound_post.read_wav(wav)
    assert sound_post.speech_level_db(audio) == pytest.approx(-25.0, abs=0.3)                 # a whisper's level
    assert facts["level_db"] == pytest.approx(-25.0, abs=0.1) and (facts["line"], facts["take"], facts["seed"]) == (1, 1, 7)
    assert len(facts["bars"]) == 60 and facts["guessed"] == []

    record = json.loads((tmp_path / "sound.provenance.json").read_text())
    assert (record["kind"], record["sound_kind"], record["schema_version"]) == ("sound", "voice", 1)
    said = record["voice"]
    assert said["text"] == "Dr. Zog owes me $5!" and said["spoken_text"] == "Doctor Zog owes me five dollars!"
    assert (said["engine"], said["voice"], said["voice_label"]) == ("qwen", "ryan", "Ryan")
    assert (said["direction"], said["language"], said["speed"], said["seed"]) == ("whisper", "english", 1.0, 7)
    assert said["direction_words"].startswith("Whisper") and (said["line"], said["of_lines"], said["take"]) == (1, 1, 1)
    assert said["cloned"] is False and said["voice_source"] == "preset voice shipped with the model"
    assert said["model"] == api.QWEN.repo and said["revision"] == api.QWEN.revision
    assert record["license"]["name"] == "Apache-2.0" and record["license"]["classification"] == "commercial-clear"
    assert any("No person's voice was copied" in term for term in record["license"]["conditions"])
    assert record["output"]["sha256"] == made["sha256"] == record["layers"][0]["sha256"]
    assert record["layers"][0]["source"]["prompt"] == "Dr. Zog owes me $5!" and "mlx-audio 0.6.7" in record["layers"][0]["source"]["runtime"]
    assert record["loudness"]["target"] == -25.0 and record["loudness"]["set_by"] == "level"
    assert record["post"]["trim"] is True and record["elapsed_seconds"] >= 0
    assert sound_api._prompt(record) == "Dr. Zog owes me $5!"                # what the Library shows


def test_a_shouted_line_comes_out_louder_than_a_whispered_one(tmp_path):
    levels = {}
    for how in ("whisper", "", "shout"):
        speaker, _ = qwen_speaker()
        request = api.clean_request({"text": HALT, "direction": how, "seed": 3, "count": 1}, api.QWEN)
        levels[how] = runner.make_one(speaker, api.QWEN, request, 1, tmp_path / (how or "plain"))["level_db"]
    assert levels["whisper"] < levels[""] < levels["shout"] and levels[""] == pytest.approx(-16.0, abs=0.2)


def test_a_line_stuck_in_a_loop_is_dropped_and_the_rest_are_made(tmp_path):
    speaker, model = qwen_speaker(runs_on={8}, silent={9})
    request = api.clean_request({"text": HALT, "seed": 7, "count": 4}, api.QWEN)
    told = []
    made = runner.make_batch(speaker, api.QWEN, request, tmp_path, tell=lambda event, **f: told.append((event, f)))
    assert [m["number"] for m in made] == [1, 4] and model.seeds == [7, 8, 9, 10]
    assert all(call["max_tokens"] == 88 for call in model.calls)            # 7 s for four words, never a minute
    problems = [f["error"] for event, f in told if event == "problem"]
    assert problems == ["Try 2: it ran on past the longest this line should take, so it was dropped.",
                        "Try 3: the sound is silent."]
    assert (tmp_path / "try-1" / "sound.wav").is_file() and not (tmp_path / "try-2").exists()


def test_several_sentences_are_several_lines_each_with_its_takes(tmp_path):
    speaker, model = qwen_speaker()
    request = api.clean_request({"text": SHOP, "seed": 100, "count": 2}, api.QWEN)
    made = runner.make_batch(speaker, api.QWEN, request, tmp_path, tell=lambda *a, **k: None)
    assert [(m["number"], m["line"], m["take"], m["seed"]) for m in made] == [
        (1, 1, 1, 100), (2, 1, 2, 101), (3, 2, 1, 102), (4, 2, 2, 103), (5, 3, 1, 104), (6, 3, 2, 105)]
    assert [call["text"] for call in model.calls][::2] == request["lines"]
    record = json.loads((tmp_path / "try-4" / "sound.provenance.json").read_text())
    assert record["voice"]["text"] == "Take a look at my wares." and record["voice"]["line"] == 2
    assert record["voice"]["of_lines"] == 3 and record["layers"][0]["source"]["batch_seed"] == 100


def test_kokoro_gets_the_voice_the_speed_and_the_accent_and_guesses_made_up_words(tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "misaki.espeak", raising=False)
    runner.use_letter_guess()
    stand_in = sys.modules["misaki.espeak"]
    assert stand_in.EspeakFallback is runner.LetterGuess                    # ours, not the GPL one
    with pytest.raises(RuntimeError, match="English only"):
        stand_in.EspeakG2P(language="es")
    model = FakeKokoro(unknown={"Eldoria"})
    request = api.clean_request({"text": "Welcome to Eldoria, Mr. Smith.", "voice": "bm_george", "speed": 1.2},
                                api.KOKORO)
    facts = runner.make_one(runner.KokoroSpeaker(model), api.KOKORO, request, 1, tmp_path)
    assert model.calls == [{"text": "Welcome to Eldoria, Mister Smith.", "voice": "bm_george", "speed": 1.2,
                            "lang_code": "b"}]
    assert facts["guessed"] == ["Eldoria"] and facts["seed"] is None
    record = json.loads((tmp_path / "sound.provenance.json").read_text())
    assert record["voice"]["guessed_words"] == ["Eldoria"] and record["voice"]["seed"] is None
    assert record["voice"]["language"] == "en-gb" and record["voice"]["direction"] is None
    assert record["layers"][0]["license"]["url"] == "https://huggingface.co/hexgrad/Kokoro-82M"
    assert sound_post.speech_level_db(sound_post.read_wav(tmp_path / "sound.wav")[0]) == pytest.approx(-16.0, abs=0.3)
    # a second line starts with a clean list of guesses
    again = runner.make_one(runner.KokoroSpeaker(model), api.KOKORO,
                            api.clean_request({"text": "Nothing odd in this one.", "voice": "bm_george"}, api.KOKORO),
                            1, tmp_path / "two")
    assert again["guessed"] == []


def test_the_letter_guess_answers_sounds_and_skips_what_has_no_letters():
    guess = runner.LetterGuess(british=True)
    assert guess(types.SimpleNamespace(text="Zog")) == ("zˈɑɡ", 1) and guess.guessed == ["Zog"]
    assert guess(types.SimpleNamespace(text="…")) == (None, None) and guess.guessed == ["Zog"]
    assert guess(types.SimpleNamespace(text="Zog.")) == ("zˈɑɡ", 1) and guess.guessed == ["Zog"]     # once, without its full stop
    assert guess(types.SimpleNamespace(text='"Eldoria,'))[0] == "ɛldˈɔɹiə" and guess.guessed == ["Zog", "Eldoria"]


# ----------------------------------------------------------------- the command and the job

def test_the_command_runs_the_runner_in_the_voice_environment(tmp_path):
    request = api.clean_request({"text": SHOP, "voice": "aiden", "direction": "calm", "seed": 5, "count": 2}, api.QWEN)
    command = api.build_command(request, tmp_path / "out", api.QWEN, tmp_path / "w")
    assert command[0] == str(api.QWEN.python) and command[1] == str(REPO / "scripts" / "voice_generate.py")
    assert command[1:].count("--text") == 3 and "--no-split" in command
    pairs = dict(zip(command[2::2], command[3::2], strict=False))
    assert pairs["--engine"] == "qwen" and pairs["--voice"] == "aiden" and pairs["--count"] == "2"
    assert command[command.index("--direction") + 1] == "calm" and command[command.index("--seed") + 1] == "5"
    assert command[command.index("--model-dir") + 1] == str(tmp_path / "w")
    own = api.clean_request({"text": HALT, "direction": "like a pirate"}, api.QWEN)
    assert api.build_command(own, tmp_path, api.QWEN)[-1] == "like a pirate"
    # the runner reads the very arguments the command carries, and lands on the same request
    parsed = api.clean_request({"lines": request["lines"], "voice": "aiden", "direction": "calm", "seed": 5,
                                "count": 2}, api.QWEN)
    assert parsed == request


STAND_IN = """
import sys
sys.path[:0] = [{repo!r}, {tests!r}]
import voice_generate as runner
from test_voice_api import FakeQwen

def load(engine, weights):
    model = FakeQwen(silent={silent!r})
    return runner.QwenSpeaker(model, model.seed)

raise SystemExit(runner.main(load=load))
"""


def stand_in_command(tmp_path, job, weights, silent=()):
    """The real command, with the model swapped for the fake one. Everything else is real:
    the runner, its arguments, its events, the levelling, the files and their records."""
    script = tmp_path / "stand_in.py"
    script.write_text(textwrap.dedent(STAND_IN).format(repo=str(REPO / "scripts"), tests=str(REPO / "tests"),
                                                       silent=set(silent)), encoding="utf-8")
    real = api.build_command(job.request, job.directory, job.engine, weights)
    return [sys.executable, str(script), *real[2:]]


def voice_job(manager, tmp_path, raw, using=()):
    job = manager.create(api.clean_request(raw, api.QWEN), api.QWEN, prepare=api.prepare, using=using)
    sound_api.run_job(job, manager, stand_in_command(tmp_path, job, weights_folder(tmp_path, api.QWEN)))
    assert job.status == "done" and job.error is None, job.describe()
    return job


def test_a_voice_job_is_a_sound_job_lines_times_takes(tmp_path):
    manager = sound_api.SoundJobManager(tmp_path / "sounds")
    job = voice_job(manager, tmp_path, {"text": SHOP, "seed": 20, "count": 2, "direction": "excited"})
    told = job.describe()
    assert told["kind"] == "voice" and told["count"] == 6 and told["engine"] == "qwen" and told["prompt"] == SHOP
    assert told["voice"]["lines"] == api.clean_request({"text": SHOP}, api.QWEN)["lines"]
    assert told["voice"]["takes"] == 2 and told["voice"]["direction"]["id"] == "excited"
    assert [(t["number"], t["line"], t["take"]) for t in told["tries"]] == [
        (1, 1, 1), (2, 1, 2), (3, 2, 1), (4, 2, 2), (5, 3, 1), (6, 3, 2)]
    assert all(t["level_db"] == pytest.approx(-14.0, abs=0.3) and t["url"].endswith(f"try-{t['number']}.wav")
               for t in told["tries"])

    kept = manager.keep(job.id, 4)                        # one line, kept on its own like any sound
    (listed,) = sound_api.kept_sounds(tmp_path)
    assert listed["file"] == kept["file"] and listed["sound_kind"] == "voice"
    assert listed["prompt"] == "Take a look at my wares." and listed["licence"] == "Apache-2.0"


def test_a_missing_engine_asks_for_setup_and_starts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "missing", lambda engine, hub=None: ["its model files (3.08 GB)"])
    monkeypatch.setattr(sound_api.subprocess, "Popen", lambda *a, **k: pytest.fail("started the engine"))
    manager = sound_api.SoundJobManager(tmp_path / "sounds")
    job = manager.create(api.clean_request({"text": HALT}, api.QWEN), api.QWEN, prepare=api.prepare)
    sound_api.run_job(job, manager)
    assert job.status == "error" and "Qwen3-TTS 1.7B CustomVoice is not set up yet" in job.error
    assert job.events[-1]["needs_setup"] is True and manager.active is None


def test_making_one_line_again_keeps_the_other_lines_tries(tmp_path):
    manager = sound_api.SoundJobManager(tmp_path / "sounds")
    first = voice_job(manager, tmp_path, {"text": SHOP, "seed": 20, "count": 1})
    again = voice_job(manager, tmp_path, {"lines": ["Take a look at my wares."], "count": 2}, using=[first.id, "nope"])
    assert first.try_file(1).is_file() and again.try_file(2).is_file()      # still on the page: not cleared
    third = voice_job(manager, tmp_path, {"text": HALT, "count": 1})          # a fresh start clears both
    assert not first.directory.exists() and not again.directory.exists() and third.try_file(1).is_file()
    assert api.clean_using([first.id, "../x", 5, "A" * 32]) == [first.id]


def test_lines_are_joined_as_one_kept_sound_with_every_line_on_its_record(tmp_path):
    root = tmp_path / "sounds"
    manager = sound_api.SoundJobManager(root)
    job = voice_job(manager, tmp_path, {"text": SHOP, "seed": 20, "count": 2, "voice": "aiden"})
    saved = api.join(root, manager, {"parts": [{"job_id": job.id, "try": 2}, {"job_id": job.id, "try": 3},
                                               {"job_id": job.id, "try": 6}], "gap_ms": 300, "name": "Shop hello"})
    assert saved["lines"] == 3 and saved["file"].startswith("sounds/Shop-hello__") and saved["licence"] == "Apache-2.0"
    folder = (tmp_path / saved["file"]).parent
    parts = [sound_post.read_wav(job.try_file(n))[0] for n in (2, 3, 6)]
    joined, _ = sound_post.read_wav(folder / "sound.wav")
    assert joined.size == sum(part.size for part in parts) + 2 * int(0.3 * 48_000)
    assert saved["seconds"] == pytest.approx(joined.size / 48_000, abs=0.001)

    record = json.loads((folder / "sound.provenance.json").read_text())
    assert record["sound_kind"] == "voice" and record["voice"]["text"] == SHOP and record["voice"]["gap_ms"] == 300
    assert (record["voice"]["engine"], record["voice"]["voice"], record["voice"]["cloned"]) == ("qwen", "aiden", False)
    assert [line["take"] for line in record["voice"]["lines"]] == [2, 1, 2]
    assert [layer["start_ms"] for layer in record["layers"]][0] == 0.0 and len(record["layers"]) == 3
    assert record["layers"][1]["start_ms"] == pytest.approx((parts[0].size / 48 + 300), abs=0.1)
    assert record["layers"][1]["sha256"] == sound_post.sha256_file(job.try_file(3))
    assert record["license"]["classification"] == "commercial-clear" and record["post"]["gain_db"] == 0.0
    assert record["license"]["name"] == "Apache-2.0" and len(record["license"]["conditions"]) == 2   # said once, not thrice
    assert record["output"]["sha256"] == sound_post.sha256_file(folder / "sound.wav")

    (listed,) = sound_api.kept_sounds(tmp_path)
    assert listed["sound_kind"] == "voice" and listed["prompt"] == SHOP and listed["layers"] == 0
    assert not [item for item in root.iterdir() if item.name.startswith(".join-")]      # nothing half-made left


@pytest.mark.parametrize("raw,error,why", [
    ({}, ValueError, "pick a take of each line"),
    ({"parts": [{"job_id": "JOB", "try": 1}] * 9}, ValueError, "join up to 8 lines"),
    ({"parts": [{"job_id": "JOB", "try": 1}], "gap_ms": 5000}, ValueError, "between 0 and 2000 ms"),
    ({"parts": [{"job_id": "JOB", "try": "1"}]}, ValueError, "a job_id and the try"),
    ({"parts": [{"job_id": "JOB", "try": 9}]}, LookupError, "no such voice line"),
    ({"parts": [{"job_id": "0" * 32, "try": 1}]}, LookupError, "no such voice line"),
])
def test_a_bad_join_saves_nothing(tmp_path, raw, error, why):
    root = tmp_path / "sounds"
    manager = sound_api.SoundJobManager(root)
    job = voice_job(manager, tmp_path, {"text": HALT, "count": 1})
    raw = json.loads(json.dumps(raw).replace("JOB", job.id))
    with pytest.raises(error, match=why):
        api.join(root, manager, raw)
    assert sound_api.kept_sounds(tmp_path) == []


def test_only_voice_lines_can_be_joined(tmp_path):
    root = tmp_path / "sounds"
    manager = sound_api.SoundJobManager(root)
    effect = manager.create(sound_api.clean_request({"prompt": "a door", "count": 1}))
    effect.tries.append({"number": 1})
    with pytest.raises(LookupError, match="no such voice line"):
        api.join(root, manager, {"parts": [{"job_id": effect.id, "try": 1}]})


# ----------------------------------------------------------------- what the sheet and agents read

def test_the_sheet_is_told_the_engines_their_voices_and_what_each_can_do(monkeypatch):
    monkeypatch.setattr(api, "missing", lambda engine, hub=None: [] if engine.id == "qwen" else ["its model files (0.34 GB)"])
    told = api.defaults_payload()
    kokoro, qwen = told["engines"]
    assert (kokoro["label"], kokoro["installed"], kokoro["download_size"], kokoro["setup_id"]) == (
        "Clear and quick", False, "0.34 GB", "voice-kokoro")
    assert (qwen["label"], qwen["installed"], qwen["tries"], qwen["directions"]) == ("With feeling", True, 4, True)
    assert kokoro["tries"] == 1 and kokoro["directions"] is False and kokoro["speed"] == {"min": 0.7, "max": 1.4}
    assert qwen["speed"] is None and kokoro["languages"] == [] and len(qwen["languages"]) == 10
    assert qwen["voices"][0] == {"id": "ryan", "label": "Ryan", "hint": "Man. Lively, with drive. English.",
                                 "language": "english"}
    assert [d["label"] for d in told["directions"]] == ["Angry", "Sad", "Whisper", "Shout", "Calm", "Excited"]
    assert told["limits"]["max_lines"] == 8 and told["join"]["gap_ms"] == 250
    # nothing a person reads on the sheet uses the shop words
    for engine in told["engines"]:
        engine.pop("model"), engine.pop("license")          # the model's own name, shown as a credit
    seen = json.dumps(told).lower()
    assert not any(word in seen for word in ("tts", "lufs", "phoneme", "sampler", "token"))
    voices = api.voices_payload()
    assert [e["id"] for e in voices["engines"]] == ["kokoro", "qwen"] and "does not copy" in voices["note"]
    assert voices["directions"] == ["angry", "sad", "whisper", "shout", "calm", "excited"]
