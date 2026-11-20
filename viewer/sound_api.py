"""Text to sound: "Make a sound" in the studio. Effects only, for now.

One engine, **MOSS-SoundEffect v2.0** in its 4-bit MLX form (Apache-2.0, about 6.4 GB), run
through the third-party `moss-sfx-mlx` port. It lives in its own Python environment
(`vendor/moss-sfx/.venv`), because the main one pins numpy below 2 and has no MLX. So this
module never imports it: it starts `scripts/sound_generate.py` with that environment's
Python and reads the lines it prints.

What the engine really offers (its `MossSoundEffectPipeline.__call__`, read 2026-11-20):
a prompt, a length up to 30 s, a step count, a guidance strength, a seed and a negative
prompt ("Leave out"). It answers one channel at 48 kHz. Text in only: it cannot take a
sound and change it. It also works on a full 30 s every time and cuts the result to
length, so a short sound takes as long as a long one.

Three rules, all carried over from our older ai-sfx project:

**Limits refuse, they do not clip.** Ask for 45 seconds and you are told the most is 30;
you do not get 30 and a surprise.

**One seed per batch.** "Make 4" picks one seed and try 1 uses it, try 2 uses seed + 1, and
so on. So any try can be made again from its own number, and the record of each says both.

**The licence travels with the file.** Every sound gets a `sound.provenance.json` beside
it, shaped for what comes next: a sound is a list of layers (today always one), so layered
sounds and variation sets can use the same record later.

**Variation sets need no model.** From a kept sound, `variations_preview` makes numbered
takes (small seeded changes of pitch and volume, `sound_post.make_variations`) in well under
a second, and `save_variation_set` writes them as `output/sounds/<name>_set__<stamp>/
<name>_01.wav ...` with one `set.provenance.json` for the lot. The same numbers and seed
always give the same takes, so nothing is stored between hearing a set and saving it.

A try lives in `output/sounds/.tries/` until you keep it. Keeping copies it to
`output/sounds/<name>__<stamp>/`, which is what the Library lists.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from image_to_3dlab import processes, sound_post, weight_gate
from image_to_3dlab.host import venv_python
from image_to_3dlab.provenance import LICENSES, write_provenance

OUTPUT_ROOT = REPO / "output" / "sounds"
TRIES_FOLDER = ".tries"          # not kept yet; never listed, never served as plain files
RUNNER = REPO / "scripts" / "sound_generate.py"
SOUND_FILE = "sound.wav"
SIDECAR_FILE = "sound.provenance.json"

MAX_PROMPT = 500
MAX_TRIES = 4
MAX_LEAVE_OUT = 8
MAX_LEAVE_OUT_WORDS = 40
# mlx seeds are 32-bit; the top of the range is left free for seed + try.
MAX_SEED = 2**31 - 1 - MAX_TRIES
SET_SIDECAR = "set.provenance.json"
SET_SUFFIX = "_set"              # a set's folder is `<name>_set__<stamp>`
SET_FILE = re.compile(r"[A-Za-z0-9_-]{1,80}_\d{2}\.wav")
PLAY_ROUNDS = 8                  # how many times round "Play like a game" is planned ahead
KEPT_RUN = re.compile(r"^[A-Za-z0-9_-]{1,80}__\d{8}-\d{6}(?:-\d+)?$")

# What the studio offers under "What kind?". Only Effect is built; the rest are shown so
# people know they are coming, and say so.
KINDS = (
    {"id": "effect", "label": "Effect", "hint": "One short sound, like a footstep or a hit.", "ready": True},
    {"id": "loop", "label": "Background loop", "hint": "Wind, rain, a crowd: plays round and round.", "ready": False},
    {"id": "music", "label": "Music", "hint": "A tune for a menu or a level.", "ready": False},
    {"id": "voice", "label": "Voice", "hint": "A line read out loud.", "ready": False},
)
# "Make variations": the same words in the studio, for agents and in the set's record.
HOW_DIFFERENT = (
    {"id": "a little", "label": "A little", "hint": "The same thing, done again. Good for footsteps."},
    {"id": "some", "label": "Some", "hint": "Clearly different takes of the same thing."},
    {"id": "a lot", "label": "A lot", "hint": "Nearly a different thing: a bigger boot, a smaller coin."},
)
DEFAULT_HOW_DIFFERENT = "a little"
WHAT_CHANGES = (
    {"id": "pitch", "label": "Pitch", "hint": "Higher or lower. Higher ones come out a little shorter, lower ones longer."},
    {"id": "volume", "label": "Volume", "hint": "Louder or softer."},
)
# Things people most often want gone from an effect. Sent as the engine's negative prompt.
LEAVE_OUT = ("music", "voices", "room echo")


@dataclass(frozen=True)
class SoundEngine:
    """One model that makes sounds: where it lives, its limits and its licence."""

    id: str                       # what the studio, agents and the sound's record call it
    label: str
    setup_id: str                 # its card on Setup (viewer/backend_catalog.py)
    licence_key: str              # its reviewed profile in provenance.LICENSES
    repo: str                     # the Hugging Face repository its weights come from
    revision: str                 # ...at the commit we pinned
    route: str
    size: str
    environment: Path             # the folder holding its own .venv
    needs: tuple[str, ...]        # files a whole download has; a half-finished one does not
    sample_rate: int
    channels: int
    min_seconds: float
    max_seconds: float
    default_seconds: float
    steps: int
    min_steps: int
    max_steps: int
    guidance: float
    leave_out: bool               # whether it takes a negative prompt

    @property
    def licence(self):
        return LICENSES[self.licence_key]

    @property
    def python(self) -> Path:
        return venv_python(self.environment)


MOSS = SoundEngine(
    id="moss", label="MOSS-SoundEffect v2.0", setup_id="sound-effects",
    licence_key="moss-soundeffect-v2",
    repo="mlx-community/MOSS-SoundEffect-v2.0-4bit",
    revision="51d53e8b51bb5f7eff1ac28ca6faf8d14557dfcb",
    route="4-bit MLX, through moss-sfx-mlx", size="6.4 GB",
    environment=REPO / "vendor" / "moss-sfx",
    needs=("model_index.json", "mlx/dit.safetensors", "mlx/vae.safetensors",
           "text_encoder/model-00001-of-00002.safetensors",
           "text_encoder/model-00002-of-00002.safetensors", "tokenizer/tokenizer.json"),
    sample_rate=48_000, channels=1,
    # The engine takes anything above 0 up to 30 s, in tenths. Half a second is our floor:
    # shorter than that and the clean-up's trim leaves next to nothing.
    min_seconds=0.5, max_seconds=30.0, default_seconds=1.5,
    # 100 steps and guidance 4.0 are the port's own defaults. Not tuned by us yet: fewer
    # steps may well do, and that wants a listening test before it becomes the default.
    steps=100, min_steps=10, max_steps=200, guidance=4.0,
    leave_out=True,
)

ENGINES = {engine.id: engine for engine in (MOSS,)}


class MissingSound(RuntimeError):
    """The engine is not installed. Names what is absent; never fetches it."""

    def __init__(self, missing: list[str], engine: SoundEngine = MOSS):
        self.missing = missing
        super().__init__(f"{engine.label} is not set up yet. Missing: " + ", ".join(missing)
                         + ". Open Setup to download it.")


def pick_engine(requested: str | None = None) -> SoundEngine:
    if requested and requested not in ENGINES:
        raise ValueError(f"no sound engine {requested!r}; choose {' or '.join(ENGINES)}")
    return ENGINES[requested] if requested else MOSS


def weights_dir(engine: SoundEngine = MOSS, hub: Path | None = None) -> Path | None:
    """The engine's weights on this disk, or None. Looks; never downloads."""
    return weight_gate.snapshot_dir(engine.repo, hub)


def missing(engine: SoundEngine = MOSS, hub: Path | None = None) -> list[str]:
    """What `engine` still needs before it can run, in plain words. Empty when ready."""
    absent = []
    if not engine.python.exists():
        absent.append("its own Python environment")
    weights = weights_dir(engine, hub)
    if weights is None:
        absent.append(f"its model files ({engine.size})")
    else:
        absent += [name for name in engine.needs if not (weights / name).exists()]
    return absent


def check_ready(engine: SoundEngine = MOSS, hub: Path | None = None) -> None:
    absent = missing(engine, hub)
    if absent:
        raise MissingSound(absent, engine)


def is_installed(engine: SoundEngine = MOSS, hub: Path | None = None) -> bool:
    return not missing(engine, hub)


def safe_name(value: str | None, limit: int = 60, fallback: str = "sound") -> str:
    """A folder-safe name from whatever was typed: letters, digits, `-` and `_` only."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-_")
    # a kept folder is `<name>__<stamp>`: a name must not carry the separator itself
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    return cleaned[:limit].strip("-_") or fallback


def try_seed(seed: int, number: int) -> int:
    """The seed for try `number` (1, 2, 3...) of a batch that started from `seed`."""
    return seed + number - 1


def _number(raw: dict[str, Any], key: str, default: float, kind: type, what: str) -> Any:
    value = raw.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a number")
    try:
        number = kind(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None
    if kind is int and float(value) != number:
        raise ValueError(f"{what} must be a whole number")
    return number


def clean_request(raw: dict[str, Any], engine: SoundEngine = MOSS) -> dict[str, Any]:
    """What to make, checked. Anything outside the engine's limits is refused with the
    limit named (ValueError), never quietly pulled inside it."""
    if not isinstance(raw, dict):
        raise ValueError("expected a description of the sound")
    kind = str(raw.get("kind") or "effect")
    known = {k["id"]: k for k in KINDS}
    if kind not in known:
        raise ValueError(f"no sound kind {kind!r}; choose {', '.join(known)}")
    if not known[kind]["ready"]:
        raise ValueError(f"{known[kind]['label']} is not built yet. Effects are.")
    prompt = " ".join(str(raw.get("prompt") or "").split())
    if not prompt:
        raise ValueError("describe the sound first")
    if len(prompt) > MAX_PROMPT:
        raise ValueError(f"the description is longer than {MAX_PROMPT} characters")
    seconds = _number(raw, "seconds", engine.default_seconds, float, "the length")
    if not engine.min_seconds <= seconds <= engine.max_seconds:      # also refuses not-a-number
        raise ValueError(f"{engine.label} makes sounds from {engine.min_seconds:g} to "
                         f"{engine.max_seconds:g} seconds long")
    steps = _number(raw, "steps", engine.steps, int, "steps")
    if not engine.min_steps <= steps <= engine.max_steps:
        raise ValueError(f"steps must be between {engine.min_steps} and {engine.max_steps}")
    count = _number(raw, "count", MAX_TRIES, int, "how many")
    if not 1 <= count <= MAX_TRIES:
        raise ValueError(f"make between 1 and {MAX_TRIES} at a time")
    seed = raw.get("seed")
    if seed is None or seed == "":
        seed = secrets.randbelow(MAX_SEED + 1)
    else:
        seed = _number(raw, "seed", 0, int, "the seed")
        if not 0 <= seed <= MAX_SEED:
            raise ValueError(f"the seed must be between 0 and {MAX_SEED}")
    leave_out = raw.get("leave_out") or []
    if isinstance(leave_out, str):
        leave_out = [part for part in leave_out.split(",")]
    if not isinstance(leave_out, list | tuple):
        raise ValueError("leave out is a list of things, like music or voices")
    leave_out = [" ".join(str(item).split()) for item in leave_out]
    leave_out = list(dict.fromkeys(item for item in leave_out if item))
    if leave_out and not engine.leave_out:
        raise ValueError(f"{engine.label} cannot be told what to leave out")
    if len(leave_out) > MAX_LEAVE_OUT or any(len(item) > MAX_LEAVE_OUT_WORDS for item in leave_out):
        raise ValueError(f"leave out at most {MAX_LEAVE_OUT} things, a few words each")
    return {
        "kind": kind, "prompt": prompt, "seconds": round(seconds, 1), "steps": steps,
        "guidance": engine.guidance, "count": count, "seed": seed, "leave_out": leave_out,
        "name": safe_name(raw.get("name") or prompt),
    }


def combined_licence(layers: list[dict[str, Any]]) -> dict[str, Any]:
    """The licence of a sound made from `layers`, by the rule the rest of the repo uses for
    anything built from several sources (`provenance.write_derived_sidecar`): one source keeps
    its licence; several join their names with " + " and pool their conditions; and when they
    are not all the same class, or any is unknown, the result is unverified."""
    unknown = {"name": "Unverified — source licence unavailable", "verified": False,
               "classification": "unverified",
               "conditions": ["Verify the source's licence before use."]}
    licences = [layer.get("license") if isinstance(layer.get("license"), dict) else unknown
                for layer in layers] or [unknown]
    if len(licences) == 1:
        merged = json.loads(json.dumps(licences[0]))
        classification = merged.get("classification") or "unverified"
    else:
        conditions: list[str] = []
        for item in licences:
            for term in item.get("conditions", []):
                if term not in conditions:
                    conditions.append(term)
        merged = {"name": " + ".join(str(item.get("name", "Unverified")) for item in licences),
                  "conditions": conditions}
        classes = {item.get("classification") or "unverified" for item in licences}
        classification = classes.pop() if len(classes) == 1 else "unverified"
    merged["classification"] = classification
    if classification == "unverified":
        merged["verified"] = False
    return merged


def engine_licence(engine: SoundEngine) -> dict[str, Any]:
    profile = engine.licence
    return {"name": profile.license_name, "url": profile.license_url,
            "classification": profile.classification, "conditions": list(profile.conditions),
            "attribution": None}


def sidecar(*, engine: SoundEngine, request: dict[str, Any], number: int, wav: Path,
            facts: dict[str, Any], post: dict[str, Any], elapsed: float | None = None) -> dict[str, Any]:
    """The record written beside a sound: what it is, how it was made, and its licence.

    `facts` is `sound_post.inspect_wav` of the finished file. A single effect is one layer;
    `variation`, `loop` and `made_for` are there, empty, for what comes next.
    """
    layer = {
        "id": "layer-1",
        "source": {
            "type": "generated",
            "engine": engine.id,
            "model": engine.repo,
            "revision": engine.revision,
            "runtime": engine.route,
            "prompt": request["prompt"],
            "leave_out": list(request.get("leave_out") or []),
            "seed": try_seed(request["seed"], number),
            "batch_seed": request["seed"],
            "try": number,
            "of": request.get("count", 1),
            "steps": request["steps"],
            "guidance": request["guidance"],
            "seconds_asked": request["seconds"],
        },
        "file": wav.name,
        "sha256": facts["sha256"],
        "license": engine_licence(engine),
        "start_ms": 0,
        "volume_db": 0.0,
        "pitch_semitones": 0,
    }
    licence = combined_licence([layer])
    record = {
        "schema_version": 1,
        "kind": "sound",
        "sound_kind": request.get("kind", "effect"),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "output": {
            "path": wav.name,
            "sha256": facts["sha256"],
            "sample_rate": facts["sample_rate"],
            "channels": facts["channels"],
            "seconds": facts["seconds"],
            "classification": licence["classification"],
        },
        "license": licence,
        "layers": [layer],
        "variation": None,
        "post": {"trim": post["trim"], "fade_ms": post["fade_ms"], "peak": post["peak"]},
        # target and measured wait for "Even volume"; the peak is what the file really has
        "loudness": {"target": None, "measured": None, "peak": facts["peak"]},
        "loop": None,
        "made_for": None,
    }
    if elapsed is not None:
        record["elapsed_seconds"] = round(elapsed, 1)
    return record


def write_sidecar(wav: Path, record: dict[str, Any]) -> Path:
    return write_provenance(wav.with_suffix(".provenance.json"), record, atomic=True)


def try_folder(root: Path, number: int, count: int) -> Path:
    """Where try `number` of `count` lands under `root`: the folder itself for a single sound."""
    return root if count == 1 else root / f"try-{number}"


def build_command(request: dict[str, Any], out: Path, engine: SoundEngine = MOSS,
                  weights: Path | None = None) -> list[str]:
    """The runner's argument list, as a pure function so a test can read it without a model."""
    command = [
        str(engine.python), str(RUNNER),
        "--engine", engine.id,
        "--prompt", request["prompt"],
        "--seconds", str(request["seconds"]),
        "--steps", str(request["steps"]),
        "--seed", str(request["seed"]),
        "--count", str(request["count"]),
        "--out", str(out),
    ]
    for item in request.get("leave_out") or []:
        command += ["--leave-out", item]
    if weights is not None:
        command += ["--model-dir", str(weights)]
    return command


class SoundJob:
    """One "Make 4": up to four tries from one description, made one after another."""

    def __init__(self, job_id: str, directory: Path, request: dict[str, Any], engine: SoundEngine = MOSS):
        self.id = job_id
        self.directory = directory
        self.request = request
        self.engine = engine
        self.status = "queued"
        self.started = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.log_lines: deque[str] = deque(maxlen=400)
        self.condition = threading.Condition()
        self.process: subprocess.Popen[bytes] | None = None
        self.cancel_requested = False
        self.error: str | None = None
        self.tries: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        event = {"elapsed_seconds": round(time.monotonic() - self.started, 1), **event}
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def set_status(self, status: str, **extra: Any) -> None:
        self.status = status
        self.emit({"status": status, **extra})

    def try_file(self, number: int) -> Path:
        return try_folder(self.directory, number, self.request["count"]) / SOUND_FILE

    def describe(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status,
            "engine": self.engine.id,
            "prompt": self.request["prompt"],
            "seconds": self.request["seconds"],
            "seed": self.request["seed"],
            "count": self.request["count"],
            "leave_out": self.request["leave_out"],
            "tries": [dict(item) for item in self.tries],
            "error": self.error,
            "elapsed_seconds": round(time.monotonic() - self.started, 1),
            "last_event": self.events[-1] if self.events else None,
            "log_tail": "\n".join(self.log_lines)[-4000:],
        }


class SoundJobManager:
    """One batch at a time. The model holds about 12 GB while it works."""

    def __init__(self, output_root: Path = OUTPUT_ROOT):
        self.output_root = output_root
        self.jobs: dict[str, SoundJob] = {}
        self.lock = threading.Lock()
        self.active: str | None = None

    def create(self, request: dict[str, Any], engine: SoundEngine = MOSS) -> SoundJob:
        with self.lock:
            running = self.jobs.get(self.active) if self.active else None
            if running and running.status in {"queued", "running", "cancelling"}:
                raise RuntimeError("a sound is already being made")
            job_id = uuid.uuid4().hex
            directory = self.output_root / TRIES_FOLDER / job_id
            # tries nobody kept, from batches before this one (or before a restart): gone, so
            # they never pile up. Nothing is running, so none of them is in use.
            tries = self.output_root / TRIES_FOLDER
            for old in (tries.iterdir() if tries.is_dir() else ()):
                shutil.rmtree(old, ignore_errors=True)
            directory.mkdir(parents=True, exist_ok=True)
            job = SoundJob(job_id, directory, request, engine)
            self.jobs[job.id] = job
            self.active = job.id
            return job

    def finish(self, job: SoundJob) -> None:
        with self.lock:
            if self.active == job.id:
                self.active = None

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.status not in {"queued", "running"}:
            return False
        job.cancel_requested = True
        job.set_status("cancelling")
        if job.process and job.process.poll() is None:
            processes.terminate_group(job.process.pid)
        return True

    def keep(self, job_id: str, number: int, name: str | None = None) -> dict[str, Any]:
        """Copy one try into the Library: `output/sounds/<name>__<stamp>/`. Keeping the same
        try twice answers the folder it already has."""
        job = self.jobs.get(job_id)
        made = next((t for t in job.tries if t["number"] == number), None) if job else None
        if made is None:
            raise ValueError("there is no such try to keep")
        if made.get("kept"):
            return {"file": made["kept"], "name": made["kept_name"]}
        source = job.try_file(number)
        record_file = source.with_suffix(".provenance.json")
        if not source.is_file() or not record_file.is_file():
            raise ValueError("that try is no longer on disk; make the sound again")
        base = safe_name(name or job.request["name"])
        stamp, n = time.strftime("%Y%m%d-%H%M%S"), 0
        target = self.output_root / f"{base}__{stamp}"
        while target.exists():      # two keeps in one second must not share a folder
            n += 1
            target = self.output_root / f"{base}__{stamp}-{n}"
        target.mkdir(parents=True)
        shutil.copy2(source, target / SOUND_FILE)
        shutil.copy2(record_file, target / SIDECAR_FILE)
        made["kept"] = f"sounds/{target.name}/{SOUND_FILE}"
        made["kept_name"] = base
        return {"file": made["kept"], "name": base}


def read_event(line: str) -> dict[str, Any] | None:
    """One line the runner printed, as an event; None for ordinary chatter."""
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        event = json.loads(text)
    except ValueError:
        return None
    return event if isinstance(event, dict) and isinstance(event.get("event"), str) else None


def failure_reason(log_lines) -> str | None:
    """The runner's last words that are not progress, for the error the page shows."""
    for line in reversed(list(log_lines)):
        text = line.strip()
        if text and read_event(text) is None:
            return text
    return None


def run_job(job: SoundJob, manager: SoundJobManager, command: list[str] | None = None) -> None:
    """Drive one batch to its end, passing on progress and each try as it lands."""
    try:
        if command is None:
            check_ready(job.engine)
            command = build_command(job.request, job.directory, job.engine, weights_dir(job.engine))
        job.set_status("running", count=job.request["count"], total_steps=job.request["steps"])
        job.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(REPO),
            **processes.group_popen_kwargs(),
        )
        job.directory.joinpath("pid").write_text(processes.pid_record(job.process.pid), encoding="utf-8")
        assert job.process.stdout is not None
        problem = None
        for raw in job.process.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = read_event(line)
            if event is None:
                job.log_lines.append(line)
                continue
            kind = event.pop("event")
            if kind == "try":
                made = {"number": int(event["number"]), "seed": event.get("seed"),
                        "seconds": event.get("seconds"), "peak": event.get("peak"),
                        "bars": event.get("bars") or [],
                        "url": f"/api/sound/{job.id}/try-{int(event['number'])}.wav",
                        "kept": None}
                job.tries.append(made)
                job.emit({"phase": "try", **made})
            elif kind == "step":
                total = event.get("total") or job.request["steps"]
                done = (int(event.get("number", 1)) - 1) + int(event.get("step", 0)) / max(1, total)
                job.emit({"phase": "making", "number": event.get("number"), "step": event.get("step"),
                          "total_steps": total,
                          "percent": round(100.0 * done / job.request["count"], 1)})
            elif kind == "loading":
                job.emit({"phase": "loading"})
            elif kind == "problem":
                problem = str(event.get("error") or "")
                job.log_lines.append(problem)
                job.emit({"phase": "problem", "number": event.get("number"), "note": problem})
        code = job.process.wait()
        if job.cancel_requested:
            job.set_status("cancelled")
        elif code != 0 or not job.tries:
            reason = problem or failure_reason(job.log_lines)
            job.error = reason or f"the sound engine stopped with code {code}"
            job.set_status("error", error=job.error, log="\n".join(list(job.log_lines)[-12:]))
        else:
            job.set_status("done", tries=len(job.tries))
    except MissingSound as exc:
        job.error = str(exc)
        job.set_status("error", error=job.error, needs_setup=True)
    except Exception as exc:
        job.error = f"{type(exc).__name__}: {exc}"
        job.set_status("error", error=job.error)
    finally:
        job.directory.joinpath("pid").unlink(missing_ok=True)
        manager.finish(job)


MANAGER = SoundJobManager()


def start(raw: dict[str, Any], engine: SoundEngine = MOSS) -> SoundJob:
    """Check the request, then make it in the background. ValueError: refused as asked;
    RuntimeError: one is already running."""
    request = clean_request(raw, engine)
    job = MANAGER.create(request, engine)
    threading.Thread(target=run_job, args=(job, MANAGER), daemon=True).start()
    return job


def defaults_payload(engine: SoundEngine = MOSS) -> dict[str, Any]:
    """What the Sound sheet needs to draw itself: the kinds, the limits, and whether the
    engine is here. `GET /api/sound/defaults`."""
    absent = missing(engine)
    return {
        "engine": engine.id, "label": engine.label, "installed": not absent, "missing": absent,
        "setup_id": engine.setup_id, "download_size": engine.size,
        "kinds": [dict(kind) for kind in KINDS],
        "leave_out": list(LEAVE_OUT) if engine.leave_out else [],
        "limits": {"min_seconds": engine.min_seconds, "max_seconds": engine.max_seconds,
                   "default_seconds": engine.default_seconds, "max_prompt": MAX_PROMPT,
                   "tries": MAX_TRIES},
        "license": {"name": engine.licence.license_name, "url": engine.licence.license_url},
        # no engine needed for these: they are made from a sound you already kept
        "variations": {"min": sound_post.MIN_VARIATIONS, "max": sound_post.MAX_VARIATIONS,
                       "default": sound_post.DEFAULT_VARIATIONS,
                       "how_different": [dict(item) for item in HOW_DIFFERENT],
                       "default_how_different": DEFAULT_HOW_DIFFERENT,
                       "change": [dict(item) for item in WHAT_CHANGES]},
    }


def display_name(stem: str) -> str:
    return stem.replace("-", " ").replace("_", " ").strip().capitalize() or "Sound"


def _record(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _prompt(record: dict[str, Any]) -> str | None:
    layers = record.get("layers") if isinstance(record.get("layers"), list) else []
    source = (layers[0].get("source") or {}) if layers and isinstance(layers[0], dict) else {}
    return source.get("prompt") if isinstance(source, dict) else None


def _licence_name(record: dict[str, Any]) -> str | None:
    licence = record.get("license") if isinstance(record.get("license"), dict) else {}
    return licence.get("name")


def kept_sounds(output: Path) -> list[dict[str, Any]]:
    """Every kept sound and saved set under `output`/sounds, newest first, each from its own
    record. A folder with no readable record is still listed (the file is there), with its
    licence unknown. A set has `kind: "sound_set"`, its numbered `files`, and `file` (its
    first), so anything that plays a sound can play a set."""
    sounds = []
    root = output / "sounds"
    if not root.is_dir():
        return sounds
    for run in root.iterdir():
        if run.name.startswith(".") or not KEPT_RUN.fullmatch(run.name) or not run.is_dir():
            continue
        wav = run / SOUND_FILE
        stem = run.name.rsplit("__", 1)[0]
        if not wav.is_file():
            if stem.endswith(SET_SUFFIX):
                listed = _kept_set(run, stem)
                if listed:
                    sounds.append(listed)
            continue
        record = _record(run / SIDECAR_FILE)
        made = record.get("output") if isinstance(record.get("output"), dict) else {}
        sounds.append({
            "id": run.name, "kind": "sound", "sound_kind": record.get("sound_kind") or "effect",
            "name": display_name(stem),
            "file": f"sounds/{run.name}/{SOUND_FILE}",
            "seconds": made.get("seconds"),
            "prompt": _prompt(record),
            "licence": _licence_name(record),
            "updated": wav.stat().st_mtime,
        })
    return sorted(sounds, key=lambda sound: sound["updated"], reverse=True)


def _kept_set(run: Path, stem: str) -> dict[str, Any] | None:
    """One saved set for the Library, or None if the folder holds no numbered sounds. The
    file names are read from the folder, never trusted from the record."""
    files = sorted(item.name for item in run.iterdir() if item.is_file() and SET_FILE.fullmatch(item.name))
    if not files:
        return None
    record = _record(run / SET_SIDECAR)
    variation = record.get("variation") if isinstance(record.get("variation"), dict) else {}
    listed = record.get("files") if isinstance(record.get("files"), list) else []
    seconds = {item.get("file"): item.get("seconds") for item in listed if isinstance(item, dict)}
    seed = variation.get("seed") if isinstance(variation.get("seed"), int) else 0
    return {
        "id": run.name, "kind": "sound_set", "sound_kind": record.get("sound_kind") or "effect",
        "name": display_name(stem[: -len(SET_SUFFIX)]),
        "file": f"sounds/{run.name}/{files[0]}",
        "files": [f"sounds/{run.name}/{name}" for name in files],
        "count": len(files),
        "seconds": seconds.get(files[0]),
        "prompt": _prompt(record),
        "licence": _licence_name(record),
        "of": variation.get("of"),
        "play_order": sound_post.play_order(len(files), PLAY_ROUNDS, seed),
        "updated": (run / files[0]).stat().st_mtime,
    }


# ----------------------------------------------------------------------------- variation sets

def kept_folder(root: Path, sound_id: Any) -> Path:
    """The folder of the kept sound `sound_id` under `root` (output/sounds), or LookupError.
    An id is a folder name and nothing else: no slashes, no dots, so it cannot point outside."""
    if not isinstance(sound_id, str) or not KEPT_RUN.fullmatch(sound_id):
        raise LookupError("there is no such kept sound")
    folder = root / sound_id
    if folder.is_symlink() or not (folder / SOUND_FILE).is_file() or folder.resolve().parent != root.resolve():
        raise LookupError("there is no such kept sound")
    return folder


def clean_variation_request(raw: dict[str, Any], *, need_seed: bool = False) -> dict[str, Any]:
    """What variations to make, checked. Outside the limits is refused (ValueError), never
    quietly pulled inside. A seed is picked when none is given, unless `need_seed`."""
    if not isinstance(raw, dict):
        raise ValueError("expected how many variations and how different")
    low, high = sound_post.MIN_VARIATIONS, sound_post.MAX_VARIATIONS
    count = _number(raw, "count", sound_post.DEFAULT_VARIATIONS, int, "how many")
    if not low <= count <= high:
        raise ValueError(f"make between {low} and {high} variations")
    how = " ".join(str(raw.get("how_different") or DEFAULT_HOW_DIFFERENT).lower().split())
    if how not in sound_post.HOW_DIFFERENT:
        raise ValueError("how different is one of: " + ", ".join(sound_post.HOW_DIFFERENT))
    change = raw.get("change")
    if change is None or change == "":
        change = [item["id"] for item in WHAT_CHANGES]
    if isinstance(change, str):
        change = change.split(",")
    if not isinstance(change, list | tuple):
        raise ValueError("what changes is a list, like pitch and volume")
    asked = {str(item).strip().lower() for item in change} - {""}
    if not asked or asked - set(sound_post.CHANGES):
        raise ValueError("pick what changes: " + ", ".join(sound_post.CHANGES))
    seed = raw.get("seed")
    if seed is None or seed == "":
        if need_seed:
            raise ValueError("the seed is missing")
        seed = secrets.randbelow(MAX_SEED + 1)
    else:
        seed = _number(raw, "seed", 0, int, "the seed")
        if not 0 <= seed <= MAX_SEED:
            raise ValueError(f"the seed must be between 0 and {MAX_SEED}")
    name = raw.get("name")
    return {"count": count, "how_different": how, "seed": seed,
            "change": [item for item in sound_post.CHANGES if item in asked],
            "name": safe_name(name) if name else None}


_LAST_TAKES: tuple[Any, list[dict[str, Any]]] | None = None     # the set just heard: played, then saved
_TAKES_LOCK = threading.Lock()


def variations_of(root: Path, sound_id: Any, request: dict[str, Any]) -> tuple[Path, list[dict[str, Any]]]:
    """The kept sound's folder and its takes for `request`. Nothing is written. The last set
    is remembered, so playing its takes one by one does not make them all again each time."""
    global _LAST_TAKES
    folder = kept_folder(root, sound_id)
    wav = folder / SOUND_FILE
    stat = wav.stat()
    key = (str(wav), stat.st_mtime_ns, stat.st_size, request["count"], request["how_different"],
           tuple(request["change"]), request["seed"])
    with _TAKES_LOCK:
        if _LAST_TAKES is None or _LAST_TAKES[0] != key:
            audio, rate = sound_post.read_wav(wav)
            _LAST_TAKES = (key, sound_post.make_variations(
                audio, rate, request["count"], request["how_different"], request["seed"], request["change"]))
        return folder, _LAST_TAKES[1]


def set_name(folder: Path, request: dict[str, Any]) -> str:
    """What the numbered files are called: the name asked for, else the kept sound's own."""
    return request.get("name") or safe_name(folder.name.rsplit("__", 1)[0])


def take_file(name: str, index: int) -> str:
    return f"{name}_{index:02d}.wav"


def _take_facts(take: dict[str, Any]) -> dict[str, Any]:
    return {key: take[key] for key in ("index", "pitch_semitones", "volume_db", "gain_db", "start_ms", "note")}


def variations_preview(root: Path, sound_id: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """Make the takes and say where each can be heard. `POST /api/sounds/<id>/variations`.
    The answer carries the seed: send the same numbers back to hear or save the very same set."""
    request = clean_variation_request(raw)
    folder, takes = variations_of(root, sound_id, request)
    name = set_name(folder, request)
    query = "count={count}&how_different={how}&change={change}&seed={seed}".format(
        count=request["count"], how=request["how_different"].replace(" ", "+"),
        change=",".join(request["change"]), seed=request["seed"])
    return {
        "sound": folder.name, "title": display_name(name), "name": name,
        "count": request["count"], "how_different": request["how_different"],
        "change": request["change"], "seed": request["seed"],
        "turned_down_db": takes[0]["gain_db"],
        "variations": [{**_take_facts(take), "file": take_file(name, take["index"]),
                        "seconds": round(take["samples"].size / sound_post.RATE, 3),
                        "bars": sound_post.wave_bars(take["samples"]),
                        "url": f"/api/sounds/{folder.name}/variations/{take['index']}.wav?{query}"}
                       for take in takes],
        "play_order": sound_post.play_order(len(takes), PLAY_ROUNDS, request["seed"]),
    }


def variation_wav(root: Path, sound_id: Any, raw: dict[str, Any], index: int) -> bytes:
    """One take of a set, as a WAV, made from the same numbers the preview answered."""
    request = clean_variation_request(raw, need_seed=True)
    _, takes = variations_of(root, sound_id, request)
    take = next((t for t in takes if t["index"] == index), None)
    if take is None:
        raise LookupError("that set has no such variation")
    return sound_post.wav_bytes(take["samples"])


def save_variation_set(root: Path, sound_id: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """Write a set to the Library: numbered WAVs in one folder, and one record for them all.

    The licence is the kept sound's own, unchanged: a variation is the same sound, moved a
    little. With no readable record beside the source, the set is unverified, by the rule the
    repo uses for anything derived (`combined_licence`).
    """
    request = clean_variation_request(raw)
    folder, takes = variations_of(root, sound_id, request)
    name = set_name(folder, request)
    source = _record(folder / SIDECAR_FILE)
    licence = combined_licence([{"license": source.get("license")}])
    making = root / f".set-{uuid.uuid4().hex}"        # hidden until whole: never half a set in the Library
    making.mkdir(parents=True)
    try:
        files = []
        for take in takes:
            wav = sound_post.write_wav(making / take_file(name, take["index"]), take["samples"])
            facts = sound_post.inspect_wav(wav)
            files.append({"file": wav.name, "sha256": facts["sha256"], "seconds": facts["seconds"],
                          "peak": facts["peak"],
                          "variation": {"of": folder.name, "seed": request["seed"], **_take_facts(take)}})
        record = {
            "schema_version": 1,
            "kind": "sound_set",
            "sound_kind": source.get("sound_kind") or "effect",
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "name": name,
            "source": {"id": folder.name, "path": f"sounds/{folder.name}/{SOUND_FILE}",
                       "sha256": sound_post.sha256_file(folder / SOUND_FILE)},
            "output": {"files": len(files), "sample_rate": sound_post.RATE, "channels": sound_post.CHANNELS,
                       "classification": licence["classification"]},
            "license": licence,
            # how the set was made: the same source, these numbers and this seed rebuild it
            "variation": {"of": folder.name, "count": request["count"], "seed": request["seed"],
                          "how_different": request["how_different"], "change": request["change"],
                          "most": dict(sound_post.HOW_DIFFERENT[request["how_different"]]),
                          "pitch_by": "resampling, so a higher take is shorter and a lower one longer",
                          "turned_down_db": takes[0]["gain_db"]},
            "files": files,
            "layers": source.get("layers") if isinstance(source.get("layers"), list) else [],
            "source_record": source or None,
        }
        write_provenance(making / SET_SIDECAR, record, atomic=True)
        stamp, n = time.strftime("%Y%m%d-%H%M%S"), 0
        target = root / f"{name}{SET_SUFFIX}__{stamp}"
        while target.exists():
            n += 1
            target = root / f"{name}{SET_SUFFIX}__{stamp}-{n}"
        os.rename(making, target)
    except BaseException:
        shutil.rmtree(making, ignore_errors=True)
        raise
    return {"id": target.name, "name": name, "folder": f"sounds/{target.name}", "record": SET_SIDECAR,
            "seed": request["seed"], "licence": licence.get("name"),
            "files": [{"file": f"sounds/{target.name}/{item['file']}", "seconds": item["seconds"],
                       "note": item["variation"]["note"]} for item in files]}
