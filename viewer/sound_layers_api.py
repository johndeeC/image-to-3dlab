"""Layers: build one sound from a few parts, each made cleanly on its own. "Layers" in the studio.

Why: the engine makes one thing well (a hit, a click) and blurs two or three things asked
for at once (a whoosh *and* a thud). So each part is made or brought in alone, placed on a
timeline with its own start, volume and pitch, and the stack is saved as one sound.

A layer comes from one of three places:

- **a try** from "Make a sound" (`add_from_try`): made by the engine, with its licence;
- **a kept sound** from the Library (`add_from_kept`): with the licence in its record;
- **your own file** (`add_from_file`): a WAV you bring, with the licence **you state** and,
  optionally, where it is from. Nothing stated is "Not checked", and so is a Creative
  Commons choice with no link to check it against. A not-checked layer makes the finished
  sound unverified.

Whichever it is, the layer is copied to `output/sounds/.layers/<id>/` (`layer.wav`, one
channel at 48 kHz, and `layer.json`), because tries are cleared by the next batch. That
folder is a workbench, not the Library: nothing in it is listed or served as a plain file,
and layers left there for a week are cleared.

The page holds the stack (which layers, where, how loud); the server holds no draft.
`mix` answers the stack as one WAV to hear, by the same code `save` writes with
(`sound_post.mix_layers`: sum, then one turn-down if it passes the peak, never a turn-up).

`save` writes a normal kept sound, so it works everywhere one does (Library, variations,
footsteps), plus every layer's own WAV beside it so it can be opened and mixed again:

    output/sounds/<name>__<stamp>/sound.wav
    output/sounds/<name>__<stamp>/sound.provenance.json     every layer, its hash and licence
    output/sounds/<name>__<stamp>/layers/layer-1.wav ...

The finished sound's licence follows the rule the repo already uses for anything built from
several sources (`sound_api.combined_licence`): names joined with " + ", and unverified
unless every layer is in the same class.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import sound_api

from image_to_3dlab import sound_post, sound_split
from image_to_3dlab.provenance import write_provenance

LAYERS_FOLDER = ".layers"          # the workbench: staged layers, never listed or served
KEPT_LAYERS = sound_api.KEPT_LAYERS
LAYER_FILE = "layer.wav"
LAYER_RECORD = "layer.json"
LAYER_ID = re.compile(r"^[0-9a-f]{32}$")
UPLOAD_LIMIT = 20 * 1024 * 1024    # 30 s of two-channel 24-bit audio at 96 kHz is 17 MB
STALE_SECONDS = 7 * 24 * 3600
MAX_LINK = 500
MAX_LABEL = 80

# What "Licence" offers for a file you bring. The words are the studio's, the agents' and
# the record's. `needs_link`: without "Where it's from" nobody can check it, so it is not checked.
LICENCES = (
    {"id": "not-checked", "label": "Not checked", "needs_link": False,
     "hint": "You are not sure. The finished sound is marked not checked too."},
    {"id": "mine", "label": "Mine", "needs_link": False,
     "hint": "You recorded or made it yourself."},
    {"id": "cc0", "label": "CC0", "needs_link": True,
     "hint": "Given away freely by its maker: any use, no credit needed. Say where it's from."},
    {"id": "cc-by", "label": "CC-BY (credit needed)", "needs_link": True,
     "hint": "Any use, as long as you credit its maker. Say where it's from."},
)
DEFAULT_LICENCE = "not-checked"
_STATED = {
    "mine": {"name": "Your own work", "url": None, "classification": "commercial-clear",
             "conditions": ["You said this file is your own work."]},
    "cc0": {"name": "CC0", "url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "classification": "commercial-clear",
            "conditions": ["You said this file is CC0. Check it at the link it came from."]},
    "cc-by": {"name": "CC-BY", "url": "https://creativecommons.org/licenses/by/4.0/",
              "classification": "commercial-conditional",
              "conditions": ["Credit the maker of this file, as CC-BY asks. Check it at the link it came from."]},
}
NOT_CHECKED = {"name": "Not checked", "url": None, "classification": "unverified", "verified": False,
               "conditions": ["Check this file's licence before you use the sound."]}


def stated_licence(choice: Any, link: str | None) -> dict[str, Any]:
    """The licence record for a file someone brought: what they said, never more. A Creative
    Commons choice with no link cannot be checked by anyone, so it is recorded as not checked."""
    choice = str(choice or DEFAULT_LICENCE).strip().lower()
    known = {item["id"]: item for item in LICENCES}
    if choice not in known:
        raise ValueError("the licence is one of: " + ", ".join(item["label"] for item in LICENCES))
    if choice == "not-checked":
        return {**NOT_CHECKED, "stated": choice, "stated_by": "you"}
    if known[choice]["needs_link"] and not link:
        return {**NOT_CHECKED, "name": f"Not checked ({known[choice]['label']} stated, no link given)",
                "stated": choice, "stated_by": "you"}
    return {**json.loads(json.dumps(_STATED[choice])), "stated": choice, "stated_by": "you",
            "attribution": link if choice == "cc-by" else None}


def clean_link(value: Any) -> str | None:
    """"Where it's from": a web address, or nothing. Anything else is refused."""
    link = " ".join(str(value or "").split())
    if not link:
        return None
    parsed = urlparse(link)
    if len(link) > MAX_LINK or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("where it's from is a web address, starting with https://")
    return link


def options_payload() -> dict[str, Any]:
    """What the Layers screen needs to draw itself. Part of `GET /api/sound/defaults`."""
    return {
        "max": sound_post.MAX_LAYERS,
        "limits": {key: {"min": low, "max": high} for key, (low, high) in sound_post.LAYER_LIMITS.items()},
        "licences": [dict(item) for item in LICENCES], "default_licence": DEFAULT_LICENCE,
        "upload_mb": UPLOAD_LIMIT // (1024 * 1024), "max_file_seconds": sound_post.MAX_IMPORT_SECONDS,
    }


def split(raw: Any) -> dict[str, Any]:
    """`POST /api/sound/layers/split` {description}: layers to make one at a time."""
    if not isinstance(raw, dict):
        raise ValueError("describe the sound first")
    return sound_split.split(raw.get("description"))


# ----------------------------------------------------------------------------- the workbench

def _bench(root: Path) -> Path:
    return root / LAYERS_FOLDER


def _clear_stale(root: Path, now: float | None = None) -> None:
    bench, now = _bench(root), time.time() if now is None else now
    for old in (bench.iterdir() if bench.is_dir() else ()):
        try:
            if now - old.stat().st_mtime > STALE_SECONDS:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            continue


def layer_folder(root: Path, layer_id: Any) -> Path:
    """The staged layer `layer_id`, or LookupError. An id is 32 hex characters and nothing
    else, so it cannot point outside the workbench."""
    if not isinstance(layer_id, str) or not LAYER_ID.fullmatch(layer_id):
        raise LookupError("there is no such layer")
    folder = _bench(root) / layer_id
    if folder.is_symlink() or not (folder / LAYER_FILE).is_file() or not (folder / LAYER_RECORD).is_file():
        raise LookupError("that layer is no longer here; add it again")
    return folder


def _describe(layer_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """A staged layer as the page and agents see it."""
    licence = record.get("license") if isinstance(record.get("license"), dict) else NOT_CHECKED
    return {"layer": layer_id, "label": record.get("label"), "from": record.get("source", {}).get("type"),
            "seconds": record.get("seconds"), "bars": record.get("bars") or [],
            "licence": licence.get("name"), "checked": licence.get("classification") != "unverified",
            "where": record.get("source", {}).get("link"),
            "url": f"/api/sound/layers/{layer_id}.wav"}


def _stage(root: Path, samples: Any, *, label: str, source: dict[str, Any], licence: dict[str, Any]) -> dict[str, Any]:
    """Write one layer to the workbench and answer it. The WAV is what every later mix reads."""
    _clear_stale(root)
    layer_id = uuid.uuid4().hex
    folder = _bench(root) / layer_id
    folder.mkdir(parents=True)
    try:
        wav = sound_post.write_wav(folder / LAYER_FILE, samples)
        facts = sound_post.inspect_wav(wav)
        record = {"label": " ".join(str(label or "Layer").split())[:MAX_LABEL] or "Layer",
                  "source": source, "license": licence, "sha256": facts["sha256"],
                  "seconds": facts["seconds"], "bars": sound_post.wave_bars(samples, 48)}
        (folder / LAYER_RECORD).write_text(json.dumps(record, indent=1), encoding="utf-8")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return _describe(layer_id, record)


def _layer_of(record: dict[str, Any]) -> dict[str, Any]:
    layers = record.get("layers") if isinstance(record.get("layers"), list) else []
    return layers[0] if layers and isinstance(layers[0], dict) else {}


def add_from_try(root: Path, manager: Any, job_id: Any, number: Any) -> dict[str, Any]:
    """Use try `number` of a "Make a sound" batch as a layer. Its record comes with it."""
    job = manager.jobs.get(job_id) if isinstance(job_id, str) else None
    made = next((t for t in job.tries if t["number"] == number), None) if job else None
    if made is None:
        raise LookupError("there is no such try to use")
    wav = job.try_file(number)
    record = sound_api._record(wav.with_suffix(".provenance.json"))
    if not wav.is_file() or not record:
        raise LookupError("that try is no longer on disk; make it again")
    samples, _ = sound_post.read_wav(wav)
    layer = _layer_of(record)
    source = dict(layer.get("source") or {"type": "generated"})
    return _stage(root, sound_post.to_mono(samples), label=source.get("prompt") or job.request["prompt"],
                  source=source, licence=layer.get("license") or record.get("license") or dict(NOT_CHECKED))


def add_from_kept(root: Path, sound_id: Any) -> dict[str, Any]:
    """Use a kept sound as a layer. Its licence is the one in its record; with no readable
    record it is not checked."""
    folder = sound_api.kept_folder(root, sound_id)
    record = sound_api._record(folder / sound_api.SIDECAR_FILE)
    samples, rate = sound_post.read_wav(folder / sound_api.SOUND_FILE)
    licence = record.get("license") if isinstance(record.get("license"), dict) else dict(NOT_CHECKED)
    source = {"type": "kept", "sound_id": folder.name, "prompt": sound_api._prompt(record),
              "sha256": sound_post.sha256_file(folder / sound_api.SOUND_FILE),
              "layers": record.get("layers") if isinstance(record.get("layers"), list) else []}
    return _stage(root, sound_post.resample(sound_post.to_mono(samples), rate),
                  label=sound_api.display_name(folder.name.rsplit("__", 1)[0]), source=source, licence=licence)


def add_from_file(root: Path, filename: Any, data: bytes, licence: Any = None, link: Any = None) -> dict[str, Any]:
    """Use your own WAV as a layer. Recorded: its name, the hash of the file as you gave it,
    what it was (rate, channels), the licence you stated and where you said it is from."""
    name = Path(str(filename or "")).name
    if Path(name).suffix.lower() != ".wav":
        raise ValueError("bring a WAV file (its name ends in .wav)")
    if not data:
        raise ValueError("that file is empty")
    if len(data) > UPLOAD_LIMIT:
        raise ValueError(f"that file is larger than {UPLOAD_LIMIT // (1024 * 1024)} MB")
    where = clean_link(link)
    stated = stated_licence(licence, where)
    samples, was = sound_post.import_wav(data)
    source = {"type": "file", "filename": sound_api.safe_name(Path(name).stem, 80, "file") + ".wav",
              "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "link": where, "was": was}
    return _stage(root, samples, label=Path(name).stem, source=source, licence=stated)


def layer_wav(root: Path, layer_id: Any) -> bytes:
    return (layer_folder(root, layer_id) / LAYER_FILE).read_bytes()


# ----------------------------------------------------------------------------- the stack

def read_stack(root: Path, raw: Any) -> list[dict[str, Any]]:
    """The stack as sent, checked, with each layer's samples and record: a list of
    `{layer, start_ms, volume_db, pitch_semitones}`. Out of range is refused (ValueError)."""
    layers = raw.get("layers") if isinstance(raw, dict) else None
    if not isinstance(layers, list) or not layers:
        raise ValueError("add a layer first")
    if len(layers) > sound_post.MAX_LAYERS:
        raise ValueError(f"a sound can have up to {sound_post.MAX_LAYERS} layers")
    stack = []
    for item in layers:
        if not isinstance(item, dict):
            raise ValueError("each layer says which sound it is and where it starts")
        numbers = sound_post.check_layer_numbers(item)
        folder = layer_folder(root, item.get("layer"))
        samples, _ = sound_post.read_wav(folder / LAYER_FILE)
        stack.append({"layer": item["layer"], "folder": folder, "samples": sound_post.to_mono(samples),
                      "record": sound_api._record(folder / LAYER_RECORD), **numbers})
    return stack


def stack_licence(stack: list[dict[str, Any]]) -> dict[str, Any]:
    """The finished sound's licence, by the repo's rule for anything built from several
    sources. Two layers under the very same licence count once, so two engine layers read
    "Apache-2.0", not "Apache-2.0 + Apache-2.0"."""
    seen: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for item in stack:
        licence = item["record"].get("license")
        licence = licence if isinstance(licence, dict) else dict(NOT_CHECKED)
        seen.setdefault(json.dumps([licence.get("name"), licence.get("classification"), licence.get("url")]), licence)
    return sound_api.combined_licence([{"license": licence} for licence in seen.values()])


_MIXES: OrderedDict[str, bytes] = OrderedDict()     # the last few mixes heard, by key
_MIX_LOCK = threading.Lock()
MIX_KEY = re.compile(r"^[0-9a-f]{32}$")
KEEP_MIXES = 4


def mix(root: Path, raw: Any) -> dict[str, Any]:
    """"Play together": the stack as one sound to hear. Nothing is written to the Library.
    `POST /api/sound/layers/mix` {layers}; the answer's `url` plays it."""
    stack = read_stack(root, raw)
    mixed, facts = sound_post.mix_layers(stack)
    licence = stack_licence(stack)
    data = sound_post.wav_bytes(mixed)
    key = hashlib.sha256(data).hexdigest()[:32]
    with _MIX_LOCK:
        _MIXES[key] = data
        _MIXES.move_to_end(key)
        while len(_MIXES) > KEEP_MIXES:
            _MIXES.popitem(last=False)
    return {**facts, "bars": sound_post.wave_bars(mixed, 90), "url": f"/api/sound/layers/mix/{key}.wav",
            "licence": licence.get("name"), "checked": licence["classification"] != "unverified"}


def mix_wav(key: Any) -> bytes:
    with _MIX_LOCK:
        data = _MIXES.get(key) if isinstance(key, str) and MIX_KEY.fullmatch(key) else None
    if data is None:
        raise LookupError("that mix is gone; press Play together again")
    return data


def save(root: Path, raw: Any) -> dict[str, Any]:
    """"Save as one sound": the mix as a kept sound, with every layer's WAV and record beside
    it. `POST /api/sound/layers/save` {layers, name?}."""
    stack = read_stack(root, raw)
    mixed, facts = sound_post.mix_layers(stack)
    licence = stack_licence(stack)
    first = stack[0]["record"]
    name = sound_api.safe_name((raw.get("name") if isinstance(raw, dict) else None) or first.get("label"))
    making = root / f".mix-{uuid.uuid4().hex}"        # hidden until whole: never half a sound in the Library
    (making / KEPT_LAYERS).mkdir(parents=True)
    try:
        wav = sound_post.write_wav(making / sound_api.SOUND_FILE, mixed)
        made = sound_post.inspect_wav(wav)
        layers = []
        for index, item in enumerate(stack, 1):
            file = f"{KEPT_LAYERS}/layer-{index}.wav"
            shutil.copyfile(item["folder"] / LAYER_FILE, making / file)
            record = item["record"]
            layers.append({
                "id": f"layer-{index}", "label": record.get("label"),
                "source": record.get("source") or {"type": "unknown"},
                "file": file, "sha256": sound_post.sha256_file(making / file),
                "license": record.get("license") if isinstance(record.get("license"), dict) else dict(NOT_CHECKED),
                "start_ms": item["start_ms"], "volume_db": item["volume_db"],
                "pitch_semitones": item["pitch_semitones"],
                "seconds": facts["layers"][index - 1]["seconds"],
            })
        record = {
            "schema_version": 1, "kind": "sound", "sound_kind": "effect",
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "output": {"path": sound_api.SOUND_FILE, "sha256": made["sha256"], "sample_rate": made["sample_rate"],
                       "channels": made["channels"], "seconds": made["seconds"],
                       "classification": licence["classification"]},
            "license": licence,
            "layers": layers,
            "variation": None,
            # layers are summed, then the sum is turned down once if it passes the peak; never up
            "post": {"mix": "sum, then one turn-down", "fade_ms": sound_post.FADE_MS, "peak": sound_post.PEAK,
                     "peak_before": facts["peak_before"], "gain_db": facts["turned_down_db"],
                     "pitch_by": "resampling, so a higher layer is shorter and a lower one longer"},
            "loudness": {"target": None, "measured": facts["loudness_db"], "measure": "average power, dB below full scale",
                         "peak": made["peak"]},
            "loop": None, "made_for": None,
        }
        write_provenance(making / sound_api.SIDECAR_FILE, record, atomic=True)
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
            "record": sound_api.SIDECAR_FILE, "seconds": made["seconds"], "layers": len(layers),
            "turned_down_db": facts["turned_down_db"],
            "licence": licence.get("name"), "checked": licence["classification"] != "unverified"}


def open_kept(root: Path, sound_id: Any) -> dict[str, Any]:
    """"Edit layers": a kept layered sound's stack, back on the workbench, with each layer
    where it was. `POST /api/sounds/<id>/layers`. The kept sound itself is not touched:
    saving again makes a new one."""
    folder = sound_api.kept_folder(root, sound_id)
    record = sound_api._record(folder / sound_api.SIDECAR_FILE)
    files = sound_api.kept_layer_files(folder, record)
    if not files:
        raise LookupError("that sound was not built from layers")
    layers = []
    for layer, path in files:
        samples, rate = sound_post.read_wav(path)
        staged = _stage(root, sound_post.resample(sound_post.to_mono(samples), rate),
                        label=layer.get("label") or layer.get("id"),
                        source=layer.get("source") if isinstance(layer.get("source"), dict) else {"type": "unknown"},
                        licence=layer.get("license") if isinstance(layer.get("license"), dict) else dict(NOT_CHECKED))
        numbers = sound_post.check_layer_numbers(layer)
        layers.append({**staged, **numbers})
    stem = folder.name.rsplit("__", 1)[0]
    return {"sound": folder.name, "name": stem, "title": sound_api.display_name(stem), "layers": layers}
