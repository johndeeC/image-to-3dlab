"""Music: "Music" on the studio's Sound page. A description in, a tune out.

One engine, **ACE-Step 1.5** (MIT), run by its makers' own `ace-step` package from an
environment of its own (`vendor/music/.venv`), started as `scripts/music_generate.py`. This
module never imports it. What it really offers on a Mac (package at commit `ca1e85fe`, read
and run 2026-11-20):

- A description (style, mood, instruments), a length, a seed, and either "instrumental" or
  lyrics to sing. It answers **two channels at 48 kHz**, which is kept: music is stereo.
- Four parts, all in one download (`PARTS`): the music model, the text reader, the sound
  decoder and the **song planner**. The planner is optional per tune ("Plan the song
  first"): it reads the description, writes a fuller one, picks a tempo and a key, and
  sketches the tune before the music model plays it. It adds a few seconds.
- The music model and the sound decoder run on MLX, the planner on mlx-lm, the text reader
  on PyTorch (Metal). 8 steps, fixed: that is what this "turbo" model is trained for.
- It can also take a recording to copy or restyle. **That is never used here**: there is no
  way to send one, a request that tries is refused, and the record says none was used.

A music job is a sound job (`sound_api.SoundJob`): same queue, same tries folder, same
status, keep and cancel. The rules are the sound ones: limits refuse and never clip, one
seed per batch (try n uses seed + n - 1), and the licence travels with the file.

**Loops** are offered: the tune is made a little longer and its end is blended into its
start (`sound_post.clean_music_loop`). It is a plain blend, not a cut on the bar line.
"""

from __future__ import annotations

import secrets
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import sound_api

from image_to_3dlab.host import venv_python
from image_to_3dlab.provenance import LICENSES

RUNNER = REPO / "scripts" / "music_generate.py"
MAX_PROMPT = 500               # the engine reads up to 512 characters of description
MAX_LYRICS = 3000              # ...and up to 4096 of lyrics
# Anything that would hand the engine a recording to copy or restyle. None of it is taken.
REFERENCE_KEYS = ("reference_audio", "src_audio", "reference", "audio", "audio_codes", "cover", "sample", "file")
NO_REFERENCE = "AssetFurnace makes music from words only. It does not copy or restyle a recording."
# What a loop's description is given, so the engine plays one even stretch with no opening
# or ending. Only when the description does not already ask for a loop.
LOOP_WORDS = "seamless loop, steady, no intro, no ending"
# The languages the engine's makers list for singing (acestep/constants.py), plus "unknown".
LANGUAGES = ("ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es", "fa", "fi", "fr", "he", "hi",
             "hr", "ht", "hu", "id", "is", "it", "ja", "ko", "la", "lt", "ms", "ne", "nl", "no", "pa", "pl",
             "pt", "ro", "ru", "sa", "sk", "sr", "sv", "sw", "ta", "te", "th", "tl", "tr", "uk", "ur", "vi",
             "yue", "zh", "unknown")


@dataclass(frozen=True)
class Part:
    """One of the models inside the download: its folder, what it does in plain words, its size."""

    folder: str
    label: str
    does: str
    size: int                 # bytes, as Hugging Face lists them
    optional: bool = False    # used only when asked for ("Plan the song first")


PARTS = (
    Part("acestep-v15-turbo", "music model", "Plays the tune.", 4_791_777_953),
    Part("acestep-5Hz-lm-1.7B", "song planner", "Plans the tune first: tempo, key, structure. Used when asked.",
         3_755_412_617, optional=True),
    Part("Qwen3-Embedding-0.6B", "text reader", "Reads your description and lyrics.", 1_207_471_006),
    Part("vae", "sound decoder", "Turns the music model's output into sound.", 337_431_813),
)


@dataclass(frozen=True)
class MusicEngine:
    """The model that makes music: where it lives, its limits and its licence."""

    id: str
    label: str
    setup_id: str
    licence_key: str
    repo: str
    revision: str
    package: str                  # the code that runs it, and the commit we read
    route: str
    size: str
    environment: Path
    needs: tuple[str, ...]        # files a whole download has
    parts: tuple[Part, ...]
    sample_rate: int
    channels: int
    min_seconds: float
    max_seconds: float
    default_seconds: float
    engine_max_seconds: float     # the engine's own limit: a loop is asked for a little longer
    steps: int
    tries: int                    # what "Make" makes
    max_tries: int

    @property
    def licence(self):
        return LICENSES[self.licence_key]

    @property
    def python(self) -> Path:
        return venv_python(self.environment)


ACE_STEP = MusicEngine(
    id="acestep", label="ACE-Step 1.5", setup_id="music", licence_key="ace-step-1.5",
    repo="ACE-Step/Ace-Step1.5", revision="19671f406d603126926c1b7e2adc169acbcade22",
    package="ace-step 1.5.0 at commit ca1e85fe9430179831e6bc6be790c332190a3866 (MIT)",
    route="MLX and PyTorch (Metal), through the ace-step package", size="10.1 GB",
    environment=REPO / "vendor" / "music",
    needs=(
        "config.json",
        "acestep-v15-turbo/config.json", "acestep-v15-turbo/configuration_acestep_v15.py",
        "acestep-v15-turbo/modeling_acestep_v15_turbo.py", "acestep-v15-turbo/model.safetensors",
        "acestep-v15-turbo/silence_latent.pt",
        "acestep-5Hz-lm-1.7B/config.json", "acestep-5Hz-lm-1.7B/model.safetensors",
        "acestep-5Hz-lm-1.7B/tokenizer.json", "acestep-5Hz-lm-1.7B/tokenizer_config.json",
        "acestep-5Hz-lm-1.7B/added_tokens.json", "acestep-5Hz-lm-1.7B/special_tokens_map.json",
        "acestep-5Hz-lm-1.7B/chat_template.jinja", "acestep-5Hz-lm-1.7B/merges.txt",
        "acestep-5Hz-lm-1.7B/vocab.json",
        "Qwen3-Embedding-0.6B/config.json", "Qwen3-Embedding-0.6B/model.safetensors",
        "Qwen3-Embedding-0.6B/tokenizer.json", "Qwen3-Embedding-0.6B/tokenizer_config.json",
        "Qwen3-Embedding-0.6B/added_tokens.json", "Qwen3-Embedding-0.6B/special_tokens_map.json",
        "Qwen3-Embedding-0.6B/chat_template.jinja", "Qwen3-Embedding-0.6B/merges.txt",
        "Qwen3-Embedding-0.6B/vocab.json",
        "vae/config.json", "vae/diffusion_pytorch_model.safetensors",
    ),
    parts=PARTS,
    sample_rate=48_000, channels=2,
    # The engine takes 10 to 600 s. Four minutes is our ceiling: a level's tune, not an album.
    min_seconds=10.0, max_seconds=240.0, default_seconds=60.0, engine_max_seconds=600.0,
    steps=8,
    # About 15 s for a 30 s tune on an M5 Max, so two tries is a short wait.
    tries=2, max_tries=4,
)

ENGINES = {engine.id: engine for engine in (ACE_STEP,)}


def pick_engine(requested: str | None = None) -> MusicEngine:
    if requested and requested not in ENGINES:
        raise ValueError(f"no music engine {requested!r}; choose {' or '.join(ENGINES)}")
    return ENGINES[requested] if requested else ACE_STEP


def missing(engine: MusicEngine = ACE_STEP, hub: Path | None = None) -> list[str]:
    """What `engine` still needs before it can run, in plain words. Empty when ready."""
    return sound_api.missing(engine, hub)


def check_ready(engine: MusicEngine = ACE_STEP, hub: Path | None = None) -> None:
    absent = missing(engine, hub)
    if absent:
        raise sound_api.MissingSound(absent, engine)


def is_installed(engine: MusicEngine = ACE_STEP, hub: Path | None = None) -> bool:
    return not missing(engine, hub)


def _flag(raw: dict[str, Any], key: str, default: bool, what: str) -> bool:
    value = raw.get(key)
    if value is None or value == "":
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{what} is on or off (true or false)")
    return value


def engine_prompt(request: dict[str, Any]) -> str:
    """The description the engine is given: a loop's gets its steadying words."""
    words = request["prompt"]
    if request.get("loop") and "loop" not in words.lower():
        return f"{words}, {LOOP_WORDS}"
    return words


def parts_used(engine: MusicEngine, plan: bool) -> list[dict[str, str]]:
    """The models inside the download that a tune ran through."""
    return [{"part": part.label, "folder": part.folder, "does": part.does}
            for part in engine.parts if plan or not part.optional]


def clean_request(raw: dict[str, Any], engine: MusicEngine = ACE_STEP) -> dict[str, Any]:
    """What to make, checked. Outside the limits is refused with the limit named
    (ValueError), never quietly pulled inside it."""
    if not isinstance(raw, dict):
        raise ValueError("expected a description of the music")
    if any(raw.get(key) for key in REFERENCE_KEYS):
        raise ValueError(NO_REFERENCE)
    prompt = " ".join(str(raw.get("prompt") or raw.get("description") or "").split())
    if not prompt:
        raise ValueError("describe the music first: style, mood, instruments")
    if len(prompt) > MAX_PROMPT:
        raise ValueError(f"the description is longer than {MAX_PROMPT} characters")
    lyrics = "\n".join(line.strip() for line in str(raw.get("lyrics") or "").strip().splitlines())
    singing = _flag(raw, "singing", bool(lyrics), "singing")
    if singing and not lyrics:
        raise ValueError("write the words to sing first, or turn singing off")
    if lyrics and not singing:
        raise ValueError("there are words to sing but singing is off; turn it on or clear the words")
    if len(lyrics) > MAX_LYRICS:
        raise ValueError(f"the words to sing are longer than {MAX_LYRICS} characters")
    language = str(raw.get("language") or "unknown").strip().lower()
    if language not in LANGUAGES:
        raise ValueError(f"no singing language {language!r}; use a code like en, es, ja, or leave it out")
    loop = _flag(raw, "loop", False, "loops")
    if loop and singing:
        raise ValueError("a loop has no singing: words would be cut where it comes round")
    seconds = sound_api._number(raw, "seconds", engine.default_seconds, float, "the length")
    if not engine.min_seconds <= seconds <= engine.max_seconds:      # also refuses not-a-number
        raise ValueError(f"{engine.label} makes music from {engine.min_seconds:g} to "
                         f"{engine.max_seconds:g} seconds long")
    count = sound_api._number(raw, "count", engine.tries, int, "how many")
    if not 1 <= count <= engine.max_tries:
        raise ValueError(f"make between 1 and {engine.max_tries} at a time")
    seed = raw.get("seed")
    if seed is None or seed == "":
        seed = secrets.randbelow(sound_api.MAX_SEED + 1)
    else:
        seed = sound_api._number(raw, "seed", 0, int, "the seed")
        if not 0 <= seed <= sound_api.MAX_SEED:
            raise ValueError(f"the seed must be between 0 and {sound_api.MAX_SEED}")
    plan = _flag(raw, "plan", True, "planning the song first")
    seconds = round(seconds, 1)
    return {
        "kind": "music", "engine": engine.id, "prompt": prompt, "singing": singing,
        "lyrics": lyrics or None, "language": language, "seconds": seconds, "loop": loop, "plan": plan,
        "steps": engine.steps, "count": count, "seed": seed,
        "name": sound_api.safe_name(raw.get("name") or prompt, fallback="music"),
        "leave_out": [],                 # what the shared job code reads (sound_api.SoundJob)
        "music_facts": {"engine": engine.id, "singing": singing, "lyrics": lyrics or None, "loop": loop,
                        "plan": plan, "seconds": seconds},
    }


def sidecar(*, engine: MusicEngine, request: dict[str, Any], number: int, wav: Path,
            facts: dict[str, Any], post: dict[str, Any], planned: dict[str, Any] | None = None,
            elapsed: float | None = None) -> dict[str, Any]:
    """The record written beside a tune: the description, the words sung or that there are
    none, which parts of the engine ran, the seed, the length, and the licence. The same shape
    as any sound's record (one layer), plus a `music` block. `planned` is what the song planner
    chose (tempo, key, its fuller description), when it ran."""
    seed = sound_api.try_seed(request["seed"], number)
    loop, level = post.get("loop"), post.get("loudness") or {}
    music = {
        "description": request["prompt"],
        "instrumental": not request["singing"],
        "lyrics": request.get("lyrics"),
        "language": request["language"] if request["singing"] else None,
        "seconds_asked": request["seconds"],
        "seed": seed,
        "planner_used": bool(request["plan"]),
        "planned": dict(planned) if planned else None,
        "engine": engine.id, "model": engine.repo, "revision": engine.revision,
        "parts_used": parts_used(engine, request["plan"]),
        "channels": facts["channels"],
        "stereo": facts["channels"] == 2,
        # the engine can restyle a recording; this tune came from words alone
        "reference_audio": None, "made_from": "words only",
        "loops": bool(loop),
    }
    layer = {
        "id": "layer-1",
        "source": {"type": "generated", "engine": engine.id, "model": engine.repo, "revision": engine.revision,
                   "runtime": f"{engine.route}; {engine.package}",
                   "prompt": request["prompt"],
                   **({"prompt_sent": engine_prompt(request)} if loop else {}),
                   "lyrics": request.get("lyrics"), "instrumental": not request["singing"],
                   "planner_used": bool(request["plan"]),
                   "seed": seed, "batch_seed": request["seed"], "try": number, "of": request.get("count", 1),
                   "steps": request["steps"], "seconds_asked": request["seconds"]},
        "file": wav.name, "sha256": facts["sha256"],
        "license": sound_api.engine_licence(engine),
        "start_ms": 0, "volume_db": 0.0, "pitch_semitones": 0,
    }
    licence = sound_api.combined_licence([layer])
    record = {
        "schema_version": 1, "kind": "sound", "sound_kind": "music",
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "output": {"path": wav.name, "sha256": facts["sha256"], "sample_rate": facts["sample_rate"],
                   "channels": facts["channels"], "seconds": facts["seconds"],
                   "classification": licence["classification"]},
        "license": licence,
        "music": music,
        "layers": [layer],
        "variation": None,
        "post": {"trim": post["trim"], "fade_ms": post["fade_ms"], "peak": post["peak"],
                 "gain_db": post.get("gain_db", 0.0), "tail_s": 0.0,
                 "fade_out_ms": post.get("fade_out_ms", post["fade_ms"]), "ended": post.get("ended"),
                 **({"join": dict(post["join"])} if loop and post.get("join") else {})},
        # music is set by its average level, in dB below full scale; the peak is what the file has
        "loudness": {"target": level.get("target"), "measured": level.get("measured"),
                     "measure": level.get("measure"), "held_by_peak": level.get("held_by_peak"),
                     "peak": facts["peak"]},
        "loop": dict(loop) if loop else None,
        "made_for": None,
    }
    if elapsed is not None:
        record["elapsed_seconds"] = round(elapsed, 1)
    return record


def build_command(request: dict[str, Any], out: Path, engine: MusicEngine = ACE_STEP,
                  weights: Path | None = None) -> list[str]:
    """The runner's argument list, as a pure function so a test can read it without a model."""
    command = [str(engine.python), str(RUNNER), "--engine", engine.id,
               "--description", request["prompt"], "--seconds", str(request["seconds"]),
               "--seed", str(request["seed"]), "--count", str(request["count"]),
               "--plan" if request["plan"] else "--no-plan", "--out", str(out)]
    if request.get("lyrics"):
        command += ["--lyrics", request["lyrics"], "--language", request["language"]]
    if request.get("loop"):
        command += ["--loop"]
    if weights is not None:
        command += ["--model-dir", str(weights)]
    return command


def prepare(job: Any) -> list[str]:
    """What `sound_api.run_job` runs for a music job, once the engine is known to be here."""
    check_ready(job.engine)
    return build_command(job.request, job.directory, job.engine, sound_api.weights_dir(job.engine))


def start(raw: dict[str, Any], engine: MusicEngine = ACE_STEP) -> Any:
    """Check the request, then make it in the background. ValueError: refused as asked;
    RuntimeError: something is already being made."""
    request = clean_request(raw, engine)
    job = sound_api.MANAGER.create(request, engine, prepare=prepare)
    threading.Thread(target=sound_api.run_job, args=(job, sound_api.MANAGER), daemon=True).start()
    return job


def defaults_payload(engine: MusicEngine = ACE_STEP) -> dict[str, Any]:
    """What the Music sheet needs to draw itself. `GET /api/music/defaults`."""
    absent = missing(engine)
    return {
        "engine": engine.id, "label": engine.label, "installed": not absent, "missing": absent,
        "setup_id": engine.setup_id, "download_size": engine.size,
        "limits": {"min_seconds": engine.min_seconds, "max_seconds": engine.max_seconds,
                   "default_seconds": engine.default_seconds, "max_prompt": MAX_PROMPT,
                   "max_lyrics": MAX_LYRICS, "tries": engine.tries, "max_tries": engine.max_tries},
        "plan": {"default": True, "label": "Plan the song first",
                 "hint": "Picks a tempo and a key and sketches the tune before playing it. "
                         "A few seconds more. Turn it off for a quicker, looser try."},
        "loop": {"default": False, "label": "Loops",
                 "hint": "Plays round and round with no click. For a level or a menu. No singing."},
        "singing": {"default": False, "label": "With singing",
                    "hint": "Off, it is instrumental: what most games want. On, write the words to sing."},
        "stereo": engine.channels == 2,
        "parts": [{"label": part.label, "does": part.does, "gb": round(part.size / 1e9, 2),
                   "optional": part.optional} for part in engine.parts],
        "no_reference": NO_REFERENCE,
        "license": {"name": engine.licence.license_name, "url": engine.licence.license_url},
    }
