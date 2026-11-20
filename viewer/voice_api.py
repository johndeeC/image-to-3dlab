"""Voice lines: "Voice" on the studio's Sound page. Words in, a spoken line out.

Two engines, both Apache-2.0, both run by **mlx-audio** (MIT) from one environment of their
own (`vendor/voice/.venv`), started as `scripts/voice_generate.py`. This module never imports
them. What each really offers (mlx-audio 0.6.7, read and run 2026-11-20):

- **Kokoro 82M** ("Clear and quick"). 24 kHz, one channel. A preset voice, a speed, American
  or British English. No feeling to choose and nothing random: the same line always comes out
  the same, so it makes one try. Under a second a line. It needs the words turned into sound
  signs first (misaki, Apache-2.0, with spaCy's small English model). Words in no dictionary
  are said from their letters by our own `voice_text.guess_phonemes`: the usual tool for that,
  espeak-ng, is GPL-3 and is **not** installed. That is also why only its English voices are
  offered: its other languages need espeak-ng or large dictionaries.
- **Qwen3-TTS 1.7B CustomVoice, 8-bit** ("With feeling"). 24 kHz, one channel. Nine preset
  speakers, ten languages, and an instruction in plain words for how the line is said. It
  writes speech 12.5 pieces a second and can get stuck repeating, so every line has a longest
  length (`voice_text.speech_tokens`). It has no speed setting. Seeded, so "Make 4" gives
  four different readings and any one can be made again from its number.

**No copying of people.** A voice is one of the presets listed here and nothing else: there
is no way to send a recording, and a request that tries is refused by name. The record of
every line says which preset spoke it.

Several sentences are several lines: each is made, heard and made again on its own, and
`join` saves the chosen ones as one sound with short gaps between them.

A voice job is a sound job (`sound_api.SoundJob`): same queue, same tries folder, same
status, keep and cancel. Try `n` of a job is take `(n - 1) % takes + 1` of line
`(n - 1) // takes + 1`.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import sound_api

from image_to_3dlab import sound_post, voice_text
from image_to_3dlab.host import venv_python
from image_to_3dlab.provenance import LICENSES, write_provenance

RUNNER = REPO / "scripts" / "voice_generate.py"
ENVIRONMENT = REPO / "vendor" / "voice"        # one environment for both engines
RUNTIME = "mlx-audio 0.6.7 (MIT)"
MAX_TEXT = voice_text.MAX_LINE * voice_text.MAX_LINES
MAX_DIRECTION = 200
GAP_MS = 250.0                  # the breath between joined lines
MAX_GAP_MS = 2000.0
# Anything that would hand the engine a recording to copy. None of it is taken.
CLONE_KEYS = ("ref_audio", "ref_text", "reference", "reference_audio", "clone", "sample", "voice_file", "audio")
NO_CLONING = ("AssetFurnace does not copy a person's voice. Pick one of the voices listed.")

# "How it's said": the words on the chip, and the plain instruction the engine is given.
# How loud each one comes out is `sound_post.VOICE_DIRECTION_DB`.
DIRECTIONS = (
    {"id": "angry", "label": "Angry", "say": "Speak in an angry, harsh tone."},
    {"id": "sad", "label": "Sad", "say": "Speak in a sad, low voice, slowly."},
    {"id": "whisper", "label": "Whisper", "say": "Whisper very quietly, as if sharing a secret."},
    {"id": "shout", "label": "Shout", "say": "Shout loudly, with full force."},
    {"id": "calm", "label": "Calm", "say": "Speak calmly and gently, at an easy pace."},
    {"id": "excited", "label": "Excited", "say": "Speak with great excitement and energy."},
)
_DIRECTION = {item["id"]: item for item in DIRECTIONS}


@dataclass(frozen=True)
class Voice:
    id: str             # what the engine calls it
    label: str
    hint: str           # one plain line: who it sounds like
    language: str       # the language it speaks best (for Kokoro, the only one)


@dataclass(frozen=True)
class VoiceEngine:
    """One model that says lines: where it lives, what it can be told, and its licence."""

    id: str
    label: str                    # the model's own name: Setup, records, agents
    choice: str                   # what the studio calls it, by what it does
    hint: str
    setup_id: str
    licence_key: str
    repo: str
    revision: str
    route: str
    size: str
    needs: tuple[str, ...]        # files a whole download has
    packages: tuple[tuple[str, str], ...]   # (folder in site-packages, plain words) it also needs
    sample_rate: int
    voices: tuple[Voice, ...]
    default_voice: str
    languages: tuple[tuple[str, str], ...]  # (id, label)
    default_language: str
    takes: int                    # tries of each line "Make" makes
    max_takes: int
    seeded: bool                  # whether two tries of a line differ
    directions: bool              # whether it can be told how to say a line
    min_speed: float
    max_speed: float
    tokens_per_second: float | None    # for the length guard; None when it cannot run on
    environment: Path = ENVIRONMENT
    channels: int = 1

    @property
    def licence(self):
        return LICENSES[self.licence_key]

    @property
    def python(self) -> Path:
        return venv_python(self.environment)

    def voice(self, voice_id: str) -> Voice | None:
        return next((voice for voice in self.voices if voice.id == voice_id), None)


def _kokoro_voice(voice_id: str, hint: str) -> Voice:
    accent = "en-us" if voice_id[0] == "a" else "en-gb"
    return Voice(voice_id, voice_id[3:].capitalize(), hint, accent)


# Who each voice is comes from the model's own VOICES.md (woman or man, accent, and its
# maker's grade for how much good audio it learnt from). Nobody here has heard all 28:
# the grades, not our ears, are behind "one of the best" and "thin".
KOKORO_VOICES = tuple(_kokoro_voice(*item) for item in (
    ("af_heart", "Woman, American. The best all-rounder."),
    ("af_bella", "Woman, American. Lively. One of the best."),
    ("af_nicole", "Woman, American. Soft and close, like a headset."),
    ("af_aoede", "Woman, American. Good."),
    ("af_kore", "Woman, American. Good."),
    ("af_sarah", "Woman, American. Good."),
    ("af_alloy", "Woman, American. Fair."),
    ("af_nova", "Woman, American. Fair."),
    ("af_sky", "Woman, American. Fair."),
    ("af_jessica", "Woman, American. Thin: try it before you rely on it."),
    ("af_river", "Woman, American. Thin: try it before you rely on it."),
    ("am_fenrir", "Man, American. Good."),
    ("am_michael", "Man, American. Good."),
    ("am_puck", "Man, American. Good."),
    ("am_echo", "Man, American. Thin: try it before you rely on it."),
    ("am_eric", "Man, American. Thin: try it before you rely on it."),
    ("am_liam", "Man, American. Thin: try it before you rely on it."),
    ("am_onyx", "Man, American. Thin: try it before you rely on it."),
    ("am_santa", "Man, American. Jolly and old. Thin."),
    ("am_adam", "Man, American. The weakest: last resort."),
    ("bf_emma", "Woman, British. The best British voice."),
    ("bf_isabella", "Woman, British. Fair."),
    ("bf_alice", "Woman, British. Thin: try it before you rely on it."),
    ("bf_lily", "Woman, British. Thin: try it before you rely on it."),
    ("bm_fable", "Man, British. Fair."),
    ("bm_george", "Man, British. Fair."),
    ("bm_lewis", "Man, British. Thin: try it before you rely on it."),
    ("bm_daniel", "Man, British. Thin: try it before you rely on it."),
))

# The nine speakers in the model's own config. Who they are is from Qwen's model card,
# in our words. Each can speak all ten languages; it sounds most at home in its own.
QWEN_VOICES = (
    Voice("ryan", "Ryan", "Man. Lively, with drive. English.", "english"),
    Voice("aiden", "Aiden", "Man, American. Sunny and clear. English.", "english"),
    Voice("vivian", "Vivian", "Young woman. Bright, a little sharp. Chinese.", "chinese"),
    Voice("serena", "Serena", "Young woman. Warm and gentle. Chinese.", "chinese"),
    Voice("uncle_fu", "Uncle Fu", "Older man. Low and mellow. Chinese.", "chinese"),
    Voice("dylan", "Dylan", "Young man from Beijing, with its accent. Chinese.", "chinese"),
    Voice("eric", "Eric", "Man from Chengdu, a little husky, with its accent. Chinese.", "chinese"),
    Voice("ono_anna", "Ono Anna", "Young woman. Light and playful. Japanese.", "japanese"),
    Voice("sohee", "Sohee", "Woman. Warm, full of feeling. Korean.", "korean"),
)

KOKORO = VoiceEngine(
    id="kokoro", label="Kokoro 82M", choice="Clear and quick",
    hint="Reads a line plainly in about a second. Pick it for narration and plain talk. English only.",
    setup_id="voice-kokoro", licence_key="kokoro-82m",
    repo="mlx-community/Kokoro-82M-bf16", revision="a71e4d38b236d968966a2002c4c895dbd12b1c3c",
    route="MLX, through mlx-audio", size="0.34 GB",
    needs=("config.json", "kokoro-v1_0.safetensors",
           *(f"voices/{voice.id}.safetensors" for voice in KOKORO_VOICES)),
    packages=(("misaki", "the package that turns English words into sounds"),
              ("en_core_web_sm", "spaCy's small English model (13 MB), which reads the sentence first")),
    sample_rate=24_000, voices=KOKORO_VOICES, default_voice="af_heart",
    languages=(("en-us", "English, American"), ("en-gb", "English, British")), default_language="en-us",
    takes=1, max_takes=1, seeded=False, directions=False,
    min_speed=0.7, max_speed=1.4, tokens_per_second=None,
)

QWEN = VoiceEngine(
    id="qwen", label="Qwen3-TTS 1.7B CustomVoice", choice="With feeling",
    hint="Can shout, whisper or sound sad, in ten languages. Pick it for characters. A few seconds a line.",
    setup_id="voice-qwen", licence_key="qwen3-tts-customvoice",
    repo="mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
    revision="41d3337e8b7f2843a75841595fc14e4b9a7a4b96",
    route="8-bit MLX, through mlx-audio", size="3.08 GB",
    needs=("config.json", "generation_config.json", "model.safetensors", "vocab.json", "merges.txt",
           "tokenizer_config.json", "preprocessor_config.json", "speech_tokenizer/model.safetensors",
           "speech_tokenizer/config.json", "speech_tokenizer/configuration.json",
           "speech_tokenizer/preprocessor_config.json"),
    packages=(),
    sample_rate=24_000, voices=QWEN_VOICES, default_voice="ryan",
    languages=(("english", "English"), ("chinese", "Chinese"), ("japanese", "Japanese"), ("korean", "Korean"),
               ("german", "German"), ("french", "French"), ("spanish", "Spanish"), ("italian", "Italian"),
               ("portuguese", "Portuguese"), ("russian", "Russian")),
    default_language="english",
    takes=4, max_takes=4, seeded=True, directions=True,
    min_speed=1.0, max_speed=1.0, tokens_per_second=12.5,
)

ENGINES = {engine.id: engine for engine in (KOKORO, QWEN)}
MAX_SEED = sound_api.MAX_SEED - voice_text.MAX_LINES * 4      # room for seed + try, every line


def pick_engine(requested: str | None = None) -> VoiceEngine:
    if requested and requested not in ENGINES:
        raise ValueError(f"no voice engine {requested!r}; choose {' or '.join(ENGINES)}")
    return ENGINES[requested] if requested else KOKORO


def package_folder(engine: VoiceEngine, name: str) -> Path | None:
    """Where the package `name` sits in the engine's environment, or None. Looks; never installs."""
    found = sorted(engine.environment.glob(f".venv/lib/python*/site-packages/{name}"))
    found += sorted(engine.environment.glob(f".venv/Lib/site-packages/{name}"))
    return found[0] if found else None


def missing(engine: VoiceEngine = KOKORO, hub: Path | None = None) -> list[str]:
    """What `engine` still needs before it can run, in plain words. Empty when ready."""
    absent = sound_api.missing(engine, hub)
    if engine.python.exists():
        absent += [words for name, words in engine.packages if package_folder(engine, name) is None]
    return absent


def check_ready(engine: VoiceEngine = KOKORO, hub: Path | None = None) -> None:
    absent = missing(engine, hub)
    if absent:
        raise sound_api.MissingSound(absent, engine)


def is_installed(engine: VoiceEngine = KOKORO, hub: Path | None = None) -> bool:
    return not missing(engine, hub)


def line_of(number: int, takes: int) -> tuple[int, int]:
    """Try `number` of a job as (which line, which take of it), both from 1."""
    return (number - 1) // takes + 1, (number - 1) % takes + 1


def try_number(line: int, take: int, takes: int) -> int:
    return (line - 1) * takes + take


def clean_direction(value: Any, engine: VoiceEngine) -> dict[str, Any] | None:
    """How the line is said: one of ours (`id`), or the user's own few words. None for plain."""
    words = " ".join(str(value or "").split())
    if not words:
        return None
    if not engine.directions:
        raise ValueError(f"{engine.choice} cannot be told how to say a line. "
                         f"{QWEN.choice} can.")
    if len(words) > MAX_DIRECTION:
        raise ValueError(f"keep how it's said under {MAX_DIRECTION} characters")
    known = _DIRECTION.get(words.lower())
    return {"id": known["id"], "words": known["say"]} if known else {"id": None, "words": words}


def clean_request(raw: dict[str, Any], engine: VoiceEngine = KOKORO) -> dict[str, Any]:
    """What to say, checked. Outside the engine's limits is refused with the limit named
    (ValueError), never quietly pulled inside it. `lines` are taken as given; `text` is split
    into sentences."""
    if not isinstance(raw, dict):
        raise ValueError("expected a line to say")
    if any(raw.get(key) for key in CLONE_KEYS):
        raise ValueError(NO_CLONING)
    if isinstance(raw.get("lines"), list | tuple) and raw["lines"]:
        lines = [" ".join(str(item or "").split()) for item in raw["lines"]]
        lines = [line for line in lines if line]
    else:
        text = str(raw.get("text") or "")
        if len(text) > MAX_TEXT:
            raise ValueError(f"that is longer than {MAX_TEXT} characters; say it in parts")
        lines = voice_text.split_sentences(text)
    if not lines:
        raise ValueError("type the line to say first")
    if len(lines) > voice_text.MAX_LINES:
        raise ValueError(f"that is {len(lines)} sentences; make up to {voice_text.MAX_LINES} at a time")
    long = next((line for line in lines if len(line) > voice_text.MAX_LINE), None)
    if long:
        raise ValueError(f"a sentence is longer than {voice_text.MAX_LINE} characters "
                         f"(\"{long[:40]}…\"). Add a full stop to break it up")
    voice_id = str(raw.get("voice") or engine.default_voice).strip()
    voice = engine.voice(voice_id) or engine.voice(voice_id.lower())
    if voice is None:
        raise ValueError(f"{engine.choice} has no voice {voice_id!r}. {NO_CLONING}")
    known = dict(engine.languages)
    language = str(raw.get("language") or "").strip().lower()
    if engine.id == KOKORO.id:
        if language and language != voice.language:
            raise ValueError(f"{voice.label} speaks {known[voice.language]}; pick another voice for another accent")
        language = voice.language
    else:
        language = language or (voice.language if voice.language in known else engine.default_language)
        if language not in known:
            raise ValueError(f"{engine.choice} speaks: " + ", ".join(known))
    speed = sound_api._number(raw, "speed", 1.0, float, "the speed")
    if not engine.min_speed <= speed <= engine.max_speed:
        if engine.min_speed == engine.max_speed:
            raise ValueError(f"{engine.choice} has no speed setting. Say it in how it's said, like \"speak slowly\"")
        raise ValueError(f"the speed must be between {engine.min_speed:g} and {engine.max_speed:g}")
    takes = sound_api._number(raw, "count", engine.takes, int, "how many")
    if not 1 <= takes <= engine.max_takes:
        if engine.max_takes == 1:
            raise ValueError(f"{engine.choice} says a line the same way every time, so it makes one try")
        raise ValueError(f"make between 1 and {engine.max_takes} tries of a line")
    seed = raw.get("seed")
    if seed is None or seed == "":
        seed = secrets.randbelow(MAX_SEED + 1)
    else:
        seed = sound_api._number(raw, "seed", 0, int, "the seed")
        if not 0 <= seed <= MAX_SEED:
            raise ValueError(f"the seed must be between 0 and {MAX_SEED}")
    direction = clean_direction(raw.get("direction"), engine)
    text = " ".join(lines)
    return {
        "kind": "voice", "engine": engine.id, "text": text, "lines": lines,
        "spoken": [voice_text.spoken(line, language) for line in lines],
        "voice": voice.id, "direction": direction, "language": language, "speed": round(speed, 2),
        "takes": takes, "count": takes * len(lines), "seed": seed,
        "name": sound_api.safe_name(raw.get("name") or text),
        # what the shared job code reads (sound_api.SoundJob): a voice line's "description" is its words
        "prompt": text, "leave_out": [],
        "voice_facts": {"engine": engine.id, "engine_label": engine.choice, "voice": voice.id,
                        "voice_label": voice.label, "language": language, "speed": round(speed, 2),
                        "direction": direction, "lines": lines, "takes": takes},
    }


def sidecar(*, engine: VoiceEngine, request: dict[str, Any], number: int, wav: Path,
            facts: dict[str, Any], post: dict[str, Any], elapsed: float | None = None,
            guessed: list[str] | None = None) -> dict[str, Any]:
    """The record written beside a voice line: the words, who said them and how, and the
    licence. The same shape as any sound's record (one layer), plus a `voice` block."""
    line, take = line_of(number, request["takes"])
    seed = sound_api.try_seed(request["seed"], number) if engine.seeded else None
    direction = request.get("direction")
    said = {
        "text": request["lines"][line - 1],
        "spoken_text": request["spoken"][line - 1],
        "engine": engine.id, "model": engine.repo, "revision": engine.revision,
        "voice": request["voice"], "voice_label": engine.voice(request["voice"]).label,
        # the consent rule, on the record: a preset, never a copy of somebody
        "voice_source": "preset voice shipped with the model", "cloned": False,
        "direction": direction["id"] if direction else None,
        "direction_words": direction["words"] if direction else None,
        "language": request["language"], "speed": request["speed"],
        "seed": seed, "line": line, "of_lines": len(request["lines"]), "take": take,
    }
    if guessed:
        said["guessed_words"] = list(guessed)      # in no dictionary: said from their letters
    layer = {
        "id": "layer-1",
        "source": {"type": "generated", "engine": engine.id, "model": engine.repo, "revision": engine.revision,
                   "runtime": f"{engine.route}; {RUNTIME}", "prompt": said["text"],
                   "voice": said["voice"], "direction": said["direction_words"], "language": said["language"],
                   "speed": said["speed"], "seed": seed, "batch_seed": request["seed"] if engine.seeded else None,
                   "try": number, "of": request["count"]},
        "file": wav.name, "sha256": facts["sha256"],
        "license": sound_api.engine_licence(engine),
        "start_ms": 0, "volume_db": 0.0, "pitch_semitones": 0,
    }
    licence = sound_api.combined_licence([layer])
    loudness = post.get("loudness") or {}
    record = {
        "schema_version": 1, "kind": "sound", "sound_kind": "voice",
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "output": {"path": wav.name, "sha256": facts["sha256"], "sample_rate": facts["sample_rate"],
                   "channels": facts["channels"], "seconds": facts["seconds"],
                   "classification": licence["classification"]},
        "license": licence,
        "voice": said,
        "layers": [layer],
        "variation": None,
        "post": {key: post.get(key) for key in ("trim", "fade_ms", "peak", "gain_db", "tail_s", "fade_out_ms")},
        "loudness": {"target": loudness.get("target"), "measured": loudness.get("measured"),
                     "level": loudness.get("level"), "measure": loudness.get("measure"),
                     "set_by": loudness.get("set_by"), "pressed_db": loudness.get("pressed_db"),
                     "held_by_peak": loudness.get("held_by_peak"),
                     "peak": facts["peak"]},
        "loop": None, "made_for": None,
    }
    if elapsed is not None:
        record["elapsed_seconds"] = round(elapsed, 1)
    return record


def build_command(request: dict[str, Any], out: Path, engine: VoiceEngine,
                  weights: Path | None = None) -> list[str]:
    """The runner's argument list, as a pure function so a test can read it without a model."""
    command = [str(engine.python), str(RUNNER), "--engine", engine.id, "--voice", request["voice"],
               "--language", request["language"], "--speed", str(request["speed"]),
               "--seed", str(request["seed"]), "--count", str(request["takes"]),
               "--no-split", "--out", str(out)]
    for line in request["lines"]:
        command += ["--text", line]
    if request.get("direction"):
        direction = request["direction"]
        command += ["--direction", direction["id"] or direction["words"]]
    if weights is not None:
        command += ["--model-dir", str(weights)]
    return command


def prepare(job: Any) -> list[str]:
    """What `sound_api.run_job` runs for a voice job, once the engine is known to be here."""
    check_ready(job.engine)
    return build_command(job.request, job.directory, job.engine, sound_api.weights_dir(job.engine))


def clean_using(value: Any) -> list[str]:
    """Earlier jobs whose tries the page still shows: ids only, and only real ones are spared."""
    items = value if isinstance(value, list | tuple) else []
    return [item for item in items if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{32}", item)][:32]


def start(raw: dict[str, Any], engine: VoiceEngine) -> Any:
    """Check the request, then say it in the background. ValueError: refused as asked;
    RuntimeError: something is already being made."""
    request = clean_request(raw, engine)
    job = sound_api.MANAGER.create(request, engine, prepare=prepare, using=clean_using(raw.get("using")))
    threading.Thread(target=sound_api.run_job, args=(job, sound_api.MANAGER), daemon=True).start()
    return job


def engine_payload(engine: VoiceEngine) -> dict[str, Any]:
    absent = missing(engine)
    return {
        "id": engine.id, "label": engine.choice, "model": engine.label, "hint": engine.hint,
        "installed": not absent, "missing": absent, "setup_id": engine.setup_id, "download_size": engine.size,
        "tries": engine.takes, "max_tries": engine.max_takes,
        "directions": engine.directions,
        "speed": None if engine.min_speed == engine.max_speed else {"min": engine.min_speed, "max": engine.max_speed},
        "voices": [{"id": v.id, "label": v.label, "hint": v.hint, "language": v.language} for v in engine.voices],
        "default_voice": engine.default_voice,
        # Kokoro's accent comes with the voice, so there is nothing to pick
        "languages": [] if engine.id == KOKORO.id else [{"id": key, "label": label} for key, label in engine.languages],
        "default_language": engine.default_language,
        "license": {"name": engine.licence.license_name, "url": engine.licence.license_url},
    }


def defaults_payload() -> dict[str, Any]:
    """What the Voice sheet needs to draw itself. `GET /api/voice/defaults`."""
    return {
        "engines": [engine_payload(engine) for engine in ENGINES.values()],
        "default_engine": KOKORO.id,
        "directions": [{"id": item["id"], "label": item["label"]} for item in DIRECTIONS],
        "limits": {"max_line": voice_text.MAX_LINE, "max_lines": voice_text.MAX_LINES, "max_text": MAX_TEXT,
                   "max_direction": MAX_DIRECTION},
        "join": {"gap_ms": GAP_MS, "max_gap_ms": MAX_GAP_MS},
        "no_cloning": NO_CLONING,
    }


def voices_payload() -> dict[str, Any]:
    """Every voice of every engine, for an agent choosing one."""
    return {"engines": [{key: told[key] for key in ("id", "label", "model", "hint", "installed", "tries",
                                                    "directions", "speed", "voices", "languages", "license")}
                        for told in (engine_payload(engine) for engine in ENGINES.values())],
            "directions": [item["id"] for item in DIRECTIONS],
            "note": NO_CLONING}


def join(root: Path, manager: Any, raw: Any) -> dict[str, Any]:
    """"Save as one": the chosen take of each line, one after another with a short gap, as
    one kept sound. `POST /api/voice/join` {parts: [{job_id, try}], gap_ms?, name?}.

    Nothing is made louder or quieter on the way (`sound_post.join_lines`): each line already
    sits at its own level, so a shouted line stays louder than the whispered one after it.
    The record lists every line as a layer, with where it starts and its own licence.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("parts"), list) or not raw["parts"]:
        raise ValueError("pick a take of each line first")
    parts = raw["parts"]
    if len(parts) > voice_text.MAX_LINES:
        raise ValueError(f"join up to {voice_text.MAX_LINES} lines")
    gap = sound_api._number(raw, "gap_ms", GAP_MS, float, "the gap")
    if not 0 <= gap <= MAX_GAP_MS:
        raise ValueError(f"the gap must be between 0 and {MAX_GAP_MS:g} ms")
    lines, records = [], []
    for part in parts:
        number = part.get("try") if isinstance(part, dict) else None
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError("each part is a job_id and the try to use")
        job = manager.jobs.get(part.get("job_id")) if isinstance(part.get("job_id"), str) else None
        if job is None or job.request.get("kind") != "voice" or not any(t["number"] == number for t in job.tries):
            raise LookupError("there is no such voice line to join")
        wav = job.try_file(number)
        record = sound_api._record(wav.with_suffix(".provenance.json"))
        if not wav.is_file() or not record:
            raise LookupError("that line is no longer on disk; make it again")
        samples, rate = sound_post.read_wav(wav)
        lines.append(sound_post.resample(sound_post.to_mono(samples), rate))
        records.append(record)
    joined, facts = sound_post.join_lines(lines, gap)
    voices = [record.get("voice") if isinstance(record.get("voice"), dict) else {} for record in records]
    text = " ".join(str(voice.get("text") or "") for voice in voices).strip()

    def same(key: str) -> Any:
        return voices[0].get(key) if all(voice.get(key) == voices[0].get(key) for voice in voices) else None

    layers = []
    for index, (record, start) in enumerate(zip(records, facts["starts_ms"], strict=True), 1):
        first = record["layers"][0] if isinstance(record.get("layers"), list) and record["layers"] else {}
        layers.append({"id": f"layer-{index}", "label": voices[index - 1].get("text"),
                       "source": first.get("source") or {"type": "unknown"}, "file": None,
                       "sha256": (record.get("output") or {}).get("sha256"),
                       "license": first.get("license") if isinstance(first.get("license"), dict) else None,
                       "start_ms": start, "volume_db": 0.0, "pitch_semitones": 0})
    # lines under one and the same licence carry it once; different ones follow the repo's
    # rule for anything built from several sources (`sound_api.combined_licence`)
    different = {json.dumps(layer["license"], sort_keys=True) for layer in layers}
    licence = sound_api.combined_licence(layers[:1] if len(different) == 1 else layers)
    name = sound_api.safe_name(raw.get("name") or text)
    making = root / f".join-{uuid.uuid4().hex}"        # hidden until whole: never half a sound in the Library
    making.mkdir(parents=True)
    try:
        wav = sound_post.write_wav(making / sound_api.SOUND_FILE, joined)
        made = sound_post.inspect_wav(wav)
        write_provenance(making / sound_api.SIDECAR_FILE, {
            "schema_version": 1, "kind": "sound", "sound_kind": "voice",
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "output": {"path": sound_api.SOUND_FILE, "sha256": made["sha256"], "sample_rate": made["sample_rate"],
                       "channels": made["channels"], "seconds": made["seconds"],
                       "classification": licence["classification"]},
            "license": licence,
            "voice": {"text": text, "lines": voices, "gap_ms": gap, "engine": same("engine"),
                      "voice": same("voice"), "language": same("language"),
                      "voice_source": "preset voices shipped with the models", "cloned": False},
            "layers": layers,
            "variation": None,
            "post": {"mix": "lines one after another with a gap; no volume change", "gap_ms": gap,
                     "fade_ms": sound_post.FADE_MS, "peak": sound_post.PEAK, "gain_db": 0.0},
            "loudness": {"target": None, "measured": sound_post.speech_level_db(joined),
                         "measure": "average power while speaking, dB below full scale", "peak": made["peak"]},
            "loop": None, "made_for": None,
        }, atomic=True)
        stamp, n = time.strftime("%Y%m%d-%H%M%S"), 0
        target = root / f"{name}__{stamp}"
        while target.exists():
            n += 1
            target = root / f"{name}__{stamp}-{n}"
        os.rename(making, target)
    except BaseException:
        shutil.rmtree(making, ignore_errors=True)
        raise
    return {"id": target.name, "name": name, "file": f"sounds/{target.name}/{sound_api.SOUND_FILE}",
            "record": sound_api.SIDECAR_FILE, "seconds": made["seconds"], "lines": len(lines),
            "licence": licence.get("name")}
