"""Footsteps on a move: find where the feet land in a character's move, and save the marks.

The finding is `image_to_3dlab/footsteps.py` (the move played back as numbers, each foot's
height watched). This module is the studio's side of it: which file a request may point at,
which kept sound the steps play, and the small file that is saved:

    output/animate/<run>/<name>_<move>.glb
    output/animate/<run>/<name>_<move>.cues.json     times and names; never audio

The cue file points at the sound's own files and at its licence record. It makes no new audio,
so it carries no licence of its own.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import sound_api

from image_to_3dlab import footsteps

# animate/<rig run>/<file>.glb and nothing else: no "..", no other folder, no deeper path
MOVE_FILE = re.compile(r"^animate/([A-Za-z0-9_-]{1,80}__rig__\d{8}-\d{6}(?:-\d+)?)/([A-Za-z0-9_.-]{1,200}\.glb)$")
SET_PLAY = "Pick one at random for each step, never the same one twice running."


def move_glb(output: Path, file: Any) -> Path:
    """The move `file` (a path under output/, as the Library gives it) on disk, or LookupError.
    Only a move beside a rigged model counts: not the rest pose, not a link, not a path that
    climbs out of its folder."""
    match = MOVE_FILE.fullmatch(file) if isinstance(file, str) else None
    if not match or ".." in match[2] or match[2].endswith("_rigged.glb"):
        raise LookupError("there is no such move")
    run = output / "animate" / match[1]
    glb = run / match[2]
    if glb.is_symlink() or not glb.is_file() or glb.resolve().parent != run.resolve():
        raise LookupError("there is no such move")
    return glb


def _json(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def motion_of(glb: Path) -> dict[str, Any]:
    """What the move's own record says about it: its name, pace and whether it loops."""
    motion = _json(glb.with_suffix(".provenance.json")).get("motion")
    return motion if isinstance(motion, dict) else {}


def move_name(glb: Path, motion: dict[str, Any] | None = None) -> str:
    """"walk" for `<name>_walk.glb`: from the record, else from the rigged model's name."""
    preset = (motion if motion is not None else motion_of(glb)).get("preset")
    if isinstance(preset, str) and preset:
        return preset
    for rigged in glb.parent.glob("*_rigged.glb"):
        base = rigged.name.removesuffix("rigged.glb")
        if glb.name.startswith(base):
            return glb.stem[len(base):]
    return glb.stem


def step_sound(output: Path, sound_id: Any, beside: Path) -> dict[str, Any]:
    """The kept sound or set `sound_id`, as the cue file names it: its files as paths from the
    cue file's own folder (`beside`), its licence, and where its licence record is."""
    kept = next((s for s in sound_api.kept_sounds(output) if s["id"] == sound_id), None) \
        if isinstance(sound_id, str) else None
    if kept is None:
        raise LookupError("there is no such kept sound")
    is_set = kept["kind"] == "sound_set"
    folder = output / "sounds" / kept["id"]
    record = folder / (sound_api.SET_SIDECAR if is_set else sound_api.SIDECAR_FILE)
    licence = _json(record).get("license")
    from_here = lambda path: Path(os.path.relpath(path, beside)).as_posix()
    return {
        "id": kept["id"], "kind": kept["kind"], "name": kept["name"],
        "files": [from_here(output / file) for file in (kept.get("files") or [kept["file"]])],
        "play": SET_PLAY if is_set else "The same sound every step.",
        "license": licence if isinstance(licence, dict) else None,
        "record": from_here(record) if record.is_file() else None,
    }


def _saved(glb: Path, output: Path) -> dict[str, Any] | None:
    record = footsteps.read_cues(glb)
    if record is None:
        return None
    sound = record.get("sound") if isinstance(record.get("sound"), dict) else {}
    return {"on": record.get("on") is not False, "sound_id": sound.get("id"),
            "file": footsteps.cues_path(glb).relative_to(output).as_posix()}


def find(output: Path, file: Any) -> dict[str, Any]:
    """The steps in one move, for showing. Writes nothing. LookupError when the file is not a
    move, or has no skeleton, move or feet in it."""
    glb = move_glb(output, file)
    motion = motion_of(glb)
    loop = motion.get("loop") if isinstance(motion.get("loop"), bool) else None
    try:
        found = footsteps.steps_in_glb(glb.read_bytes(), loop=loop)
    except ValueError as exc:
        raise LookupError(str(exc)) from exc
    speed = motion.get("speed") if isinstance(motion.get("speed"), int | float) else None
    label = motion.get("label") if isinstance(motion.get("label"), str) else None
    return {**found, "file": file, "move": move_name(glb, motion), "label": label, "speed": speed,
            "words": footsteps.in_words(found), "saved": _saved(glb, output)}


def save(output: Path, raw: dict[str, Any]) -> dict[str, Any]:
    """Find the steps in `raw["file"]` and write `<move>.cues.json` beside it. `sound_id` is
    the kept sound or set the steps play (none is fine: the marks still say when). `on: false`
    keeps the marks but tells a game not to play them."""
    if not isinstance(raw.get("on", True), bool):
        raise ValueError("on is true or false")
    found = find(output, raw.get("file"))
    glb = move_glb(output, raw.get("file"))
    sound = step_sound(output, raw["sound_id"], glb.parent) if raw.get("sound_id") else None
    record = footsteps.cue_record(found, move=found["move"], model=glb.name, speed=found["speed"],
                                  sound=sound, on=raw.get("on", True))
    path = footsteps.write_cues(glb, record)
    return {**found, "saved": _saved(glb, output), "cues_file": path.relative_to(output).as_posix(),
            "sound": sound, "on": record["on"]}
