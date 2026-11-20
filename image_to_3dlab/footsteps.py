"""Footstep marks on a move: when each foot lands, written as a small list of times.

Games do not want a walk with the sound baked in. They want to know *when* a foot lands, and
play a sound there themselves (Unity Animation Events, Godot method tracks, Roblox keyframe
markers). So this finds the landings in a finished move and writes `<move>.cues.json` beside it.

How the steps are found: the move itself is played back as numbers (`glb_motion.joint_tracks`)
and each foot's height is watched. That reads what really ships, so it works for any rig and any
move, whoever made it, and a faster move gives closer steps with no extra sum. A foot is *down*
when it is near the lowest it ever gets and *up* when it is well clear, with a gap between the two
so a foot hovering at the line does not count as ten steps. A landing is a step.

Everything above `steps_in_glb` is plain arrays in, plain lists out. `gait_steps` is the same
answer worked out from a known gait (`scripts/quadruped_gait.py`'s phases and duty) instead of
from watching; the tests hold the two to each other.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Any

import numpy as np

from . import glb_motion
from .glb_turn import parse_glb

SCHEMA_VERSION = 1
CUES_SUFFIX = ".cues.json"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# A foot has to rise this share of the body's height at some point, or it never left the floor:
# a walk lifts a foot 3-5%, a restless idle or a fighting stance about 1%.
MIN_LIFT_SHARE = 0.02
DOWN_SHARE = 0.25          # of the foot's own highest lift: at or under this, it is on the floor
UP_SHARE = 0.5             # at or over this, it is in the air; between the two, nothing changes
MIN_SPAN_SECONDS = 0.05    # shorter on the floor (a toe brushing it mid-swing) or in the air: ignored
SETTLE_SECONDS = 0.06      # how long a landing is followed down to the floor
SETTLE_SHARE = 0.02        # ... while it still drops this share of its lift every sample
MIN_GAP_SECONDS = 0.1      # one foot cannot land twice this close together
TOGETHER_SECONDS = 0.04    # feet landing this close make one sound, not two
LOOP_GAP_SHARE = 0.03      # first and last pose this close (of body height): the move loops
FLOOR_SHARE = 0.15         # with no humanoid found: a chain's end this near the lowest joint is a foot
HOW = "Played the move back and watched each foot's height."
HOW_GAIT = "Worked out from the gait the move was built with."


# ----------------------------------------------------------------------------- one foot

def foot_clearance(heights: np.ndarray) -> np.ndarray:
    """How far a foot is off the floor at each moment. `heights` is (moments, joints): the
    foot's joints (ankle, toe). Each is measured from its own lowest point, and the foot is as
    low as its lowest joint: heel down with the toe up is down, and so is toe down with the
    heel up."""
    heights = np.asarray(heights, dtype=float)
    if heights.ndim == 1:
        heights = heights[:, None]
    return (heights - heights.min(axis=0)).min(axis=1)


def _states(clearance: np.ndarray, down: float, up: float, loop: bool) -> np.ndarray:
    """True where the foot is down, with the gap between `down` and `up` holding the last answer."""
    sure = np.where(clearance <= down, 1, np.where(clearance >= up, -1, 0))
    known = np.flatnonzero(sure)
    if not len(known):
        return np.ones(len(clearance), dtype=bool)
    state = sure[known[-1]] if loop else (1 if clearance[0] < up else -1)
    out = np.empty(len(clearance), dtype=bool)
    for i, value in enumerate(sure):
        state = value or state
        out[i] = state > 0
    return out


def _runs(flags: np.ndarray, loop: bool) -> list[tuple[int, int, bool]]:
    """(start, length, value) for each run of equal flags. On a loop the run crossing the end
    is one run, starting before the end; a foot that never changes is one run of everything."""
    n = len(flags)
    edges = [i for i in range(n) if flags[i] != flags[i - 1]] if loop else \
        [0] + [i for i in range(1, n) if flags[i] != flags[i - 1]]
    if not edges:
        return [(0, n, bool(flags[0]))]
    runs = []
    for k, start in enumerate(edges):
        end = edges[k + 1] if k + 1 < len(edges) else (edges[0] + n if loop else n)
        runs.append((start, end - start, bool(flags[start])))
    return runs


def _drop_short(flags: np.ndarray, value: bool, fewest: int, loop: bool) -> np.ndarray:
    out = flags.copy()
    runs = _runs(flags, loop)
    if len(runs) < 2:
        return out
    for start, length, is_value in runs:
        if is_value == value and length < fewest:
            out[np.arange(start, start + length) % len(flags)] = not value
    return out


def _crossing(times: np.ndarray, clearance: np.ndarray, i: int, level: float, step: float) -> float:
    """The moment between sample i-1 and i when the foot passes `level`."""
    a, b = clearance[i - 1], clearance[i]
    share = 0.0 if a == b else float(np.clip((a - level) / (a - b), 0.0, 1.0))
    return float(times[i] - step + share * step)


def _landing(clearance: np.ndarray, start: int, down: float, lift: float, step: float, loop: bool) -> float:
    """When the foot that came under `down` at sample `start` is really on the floor: it is
    followed for as long as it is still dropping fast (a sound that comes before the foot
    lands is worse than one a moment after), but never for longer than SETTLE_SECONDS."""
    n, at = len(clearance), start
    while ((at + 1 < n or loop) and (at + 1 - start) * step <= SETTLE_SECONDS
           and clearance[at % n] - clearance[(at + 1) % n] > SETTLE_SHARE * lift):
        at += 1
    if at > start:
        return at * step
    return _crossing(np.arange(n) * step, clearance, start, down, step)


def foot_contacts(times, clearance, *, seconds: float, loop: bool, body_height: float) -> dict[str, Any]:
    """One foot's time on the floor and its landings.

    `times` are evenly spaced from 0 to `seconds`; on a loop the last moment is the first one
    again. Returns `planted` ([[from, to], ...] in seconds; on a loop a span may run past the
    end, meaning it carries on from the start), `steps` (landing times) and `lift` (the highest
    the foot gets). A foot that never rises `MIN_LIFT_SHARE` of the body's height has no steps.
    """
    times = np.asarray(times, dtype=float)
    clearance = np.asarray(clearance, dtype=float)
    if loop and len(times) > 2:
        times, clearance = times[:-1], clearance[:-1]
    step = float(times[1] - times[0]) if len(times) > 1 else 0.0
    lift = float(clearance.max()) if len(clearance) else 0.0
    if lift < MIN_LIFT_SHARE * body_height or step <= 0:
        return {"planted": [[0.0, round(float(seconds), 4)]], "steps": [], "lift": round(lift, 4)}
    down, up = DOWN_SHARE * lift, UP_SHARE * lift
    fewest = max(1, int(np.ceil(MIN_SPAN_SECONDS / step - 1e-9)))
    flags = _states(clearance, down, up, loop)
    flags = _drop_short(flags, True, fewest, loop)
    flags = _drop_short(flags, False, fewest, loop)

    planted, steps = [], []
    n = len(flags)
    runs = _runs(flags, loop)
    for start, length, is_down in runs:
        if not is_down:
            continue
        landed = len(runs) > 1 and (loop or start > 0)
        begin = _landing(clearance, start, down, lift, step, loop) if landed else float(times[start])
        last = start + length              # the span ends when the foot leaves the floor, not when it is well clear
        while last - 1 > start and clearance[(last - 1) % n] > down:
            last -= 1
        finish = last * step if loop or start + length < n else float(seconds)
        if loop and landed:
            begin = round(begin, 4) % seconds
            finish = begin + (finish - begin) % seconds
        planted.append([round(begin, 4), round(finish, 4)])
        if landed:
            steps.append(round(begin, 4))
    steps.sort()
    kept: list[float] = []
    for t in steps:
        if not kept or t - kept[-1] >= MIN_GAP_SECONDS:
            kept.append(t)
    if loop and len(kept) > 1 and kept[0] + seconds - kept[-1] < MIN_GAP_SECONDS:
        kept.pop()
    return {"planted": sorted(planted), "steps": kept, "lift": round(lift, 4)}


# ----------------------------------------------------------------------------- all the feet

def _number(feet: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cues in time order from every foot's steps; feet landing together share one. Each foot's
    steps are rewritten as {time_s, n}: `n` is the number of the cue that plays for it."""
    landings = sorted((t, i) for i, foot in enumerate(feet) for t in foot["steps"])
    cues: list[dict[str, Any]] = []
    marks: list[list[dict[str, Any]]] = [[] for _ in feet]
    for t, i in landings:
        name = feet[i]["foot"]
        if cues and t - cues[-1]["time_s"] <= TOGETHER_SECONDS and name not in cues[-1]["foot"].split(" + "):
            cues[-1]["foot"] += f" + {name}"
        else:
            cues.append({"time_s": t, "foot": name})
        marks[i].append({"time_s": t, "n": len(cues)})
    for foot, mine in zip(feet, marks):
        foot["steps"] = mine
    return cues


def find_steps(times, feet: dict[str, np.ndarray], *, seconds: float, loop: bool,
               body_height: float) -> dict[str, Any]:
    """Every foot's landings in one move. `feet` is {name: heights (moments, joints)}.

    Returns `feet` ([{foot, planted, steps: [{time_s, n}], lift}]), `cues` ([{time_s, foot}], in
    time order) plus `seconds` and `loop`.
    """
    rows = [{"foot": name, **foot_contacts(times, foot_clearance(heights), seconds=seconds, loop=loop,
                                           body_height=body_height)}
            for name, heights in feet.items()]
    return {"seconds": round(float(seconds), 4), "loop": bool(loop), "cues": _number(rows), "feet": rows}


def gait_steps(phases: dict[str, float], duty: float, seconds: float) -> dict[str, Any]:
    """The same answer for a gait whose timing is known rather than watched: each foot lands at
    its `phase` (a share of the loop) and stays down for `duty` of it. That is exactly what
    `scripts/quadruped_gait.py` builds a walk or trot from (`GAITS[...]["phases"]`, `["duty"]`)."""
    if not 0.0 < duty < 1.0 or seconds <= 0:
        raise ValueError("duty is between 0 and 1, and the loop has a length")
    rows = []
    for name, phase in phases.items():
        start = (phase % 1.0) * seconds
        rows.append({"foot": name, "planted": [[round(start, 4), round(start + duty * seconds, 4)]],
                     "steps": [round(start, 4)], "lift": None})
    return {"seconds": round(float(seconds), 4), "loop": True, "cues": _number(rows), "feet": rows}


def at_speed(cues: list[dict[str, Any]], speed: float) -> list[dict[str, Any]]:
    """The cues for the same move played `speed` times as fast: twice as fast, half the times."""
    if speed <= 0:
        raise ValueError("speed must be more than zero")
    return [{**cue, "time_s": round(cue["time_s"] / speed, 4)} for cue in cues]


def looks_like_loop(positions: np.ndarray, body_height: float) -> bool:
    """Does the move end in the pose it started in? `positions` is (moments, joints, 3)."""
    gap = float(np.abs(np.asarray(positions[0]) - np.asarray(positions[-1])).max())
    return gap <= LOOP_GAP_SHARE * body_height


def in_words(found: dict[str, Any]) -> str:
    """One line for a person: "4 steps in each 1.2 s loop: left, right, left, right."."""
    cues = found.get("cues") or []
    length = f"{round(float(found.get('seconds') or 0), 2):g} s"
    if not cues:
        return "No steps found: the feet stay on the floor in this move."
    count = f"{len(cues)} step{'' if len(cues) == 1 else 's'}"
    where = f"in each {length} loop" if found.get("loop") else f"in {length}"
    return f"{count} {where}: {', '.join(cue['foot'] for cue in cues)}."


# ----------------------------------------------------------------------------- which bones are feet

def _children(parents: dict[str, str | None]) -> dict[str, list[str]]:
    kids: dict[str, list[str]] = {name: [] for name in parents}
    for name, parent in parents.items():
        if parent in kids:
            kids[parent].append(name)
    return kids


def humanoid_feet(parents: dict[str, str | None], rest: dict[str, np.ndarray]) -> dict[str, list[str]] | None:
    """{"left": [ankle, toe], "right": [...]} from the mapper the preset moves use (it reads the
    skeleton's shape, never bone names), or None when it finds no two-legged body."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    try:
        from kimodo_retarget import map_skintokens_to_soma
        mapping, _ = map_skintokens_to_soma(parents, rest, up=np.array([0.0, 1.0, 0.0]))
    except (ImportError, ValueError, KeyError, IndexError, StopIteration):
        return None
    feet = {side.lower(): [bone for bone, role in mapping.items() if role in (f"{side}Foot", f"{side}ToeBase")]
            for side in ("Left", "Right")}
    return feet if all(feet.values()) else None


def lowest_ends(parents: dict[str, str | None], rest: dict[str, np.ndarray]) -> dict[str, list[str]]:
    """Feet for any other skeleton: the chain ends that rest near the floor, each with the
    joint above it. Named by where they stand: left/right (glTF's +X is the model's left), and
    front/back as well when there are four. Any other number is "foot 1", "foot 2", ..."""
    if not parents:
        return {}
    kids = _children(parents)
    low = min(float(p[1]) for p in rest.values())
    tall = max(float(p[1]) for p in rest.values()) - low
    reach = low + FLOOR_SHARE * tall
    ends = [name for name in parents if not kids[name] and float(rest[name][1]) <= reach]
    groups = {end: [parents[end], end] if parents[end] and float(rest[parents[end]][1]) <= low + 2 * FLOOR_SHARE * tall
              else [end] for end in ends}
    side = lambda name: "left" if float(rest[name][0]) >= 0 else "right"
    if len(ends) == 2 and {side(e) for e in ends} == {"left", "right"}:
        return {side(e): groups[e] for e in sorted(ends, key=side)}
    if len(ends) == 4:
        middle = float(np.mean([rest[e][2] for e in ends]))
        named = {f"{'front' if float(rest[e][2]) >= middle else 'back'} {side(e)}": groups[e] for e in ends}
        if len(named) == 4:
            return dict(sorted(named.items()))
    ordered = sorted(ends, key=lambda e: (-float(rest[e][2]), -float(rest[e][0])))
    return {f"foot {i + 1}": groups[e] for i, e in enumerate(ordered)}


def find_feet(parents: dict[str, str | None], rest: dict[str, np.ndarray]) -> dict[str, list[str]]:
    return humanoid_feet(parents, rest) or lowest_ends(parents, rest)


# ----------------------------------------------------------------------------- a real file

def steps_in_tracks(tracks: dict[str, Any], *, loop: bool | None = None) -> dict[str, Any]:
    """`find_steps` on what `glb_motion.joint_tracks` returns. `loop=None` looks at the move:
    it loops if it ends in the pose it started in."""
    names = tracks["names"]
    rest = dict(zip(names, np.asarray(tracks["rest"], dtype=float)))
    if not names:
        raise LookupError("this model has no skeleton")
    body_height = float(tracks["rest"][:, 1].max() - tracks["rest"][:, 1].min()) or 1.0
    feet = find_feet(tracks["parents"], rest)
    if not feet:
        raise LookupError("no feet found on this skeleton")
    loops = looks_like_loop(tracks["positions"], body_height) if loop is None else bool(loop)
    heights = {name: tracks["positions"][:, [names.index(b) for b in bones], 1] for name, bones in feet.items()}
    found = find_steps(tracks["times"], heights, seconds=tracks["seconds"], loop=loops, body_height=body_height)
    found["found"] = {"how": HOW, "samples_per_second": glb_motion.SAMPLE_FPS, "feet": feet}
    return found


def steps_in_glb(data: bytes, *, loop: bool | None = None) -> dict[str, Any]:
    """The footsteps in an animated GLB's first move. Raises LookupError when the file has no
    skeleton, no move or no feet, and ValueError when it is not a GLB that can be read."""
    try:
        document, binary = parse_glb(data)
    except struct.error as exc:
        raise ValueError("not a GLB file") from exc
    return steps_in_tracks(glb_motion.joint_tracks(document, binary), loop=loop)


# ----------------------------------------------------------------------------- the cue file

def cues_path(move_glb: Path) -> Path:
    """`<move>.cues.json`, beside the move it belongs to."""
    return move_glb.with_name(move_glb.stem + CUES_SUFFIX)


def cue_record(found: dict[str, Any], *, move: str, model: str, speed: float | None = None,
               sound: dict[str, Any] | None = None, on: bool = True) -> dict[str, Any]:
    """What goes in `<move>.cues.json`. It holds times and names, never audio.

    `sound` is the kept sound or set the steps play: {id, kind, name, files, license, record}
    with paths as the game will find them from the cue file. Each cue repeats the id and files,
    so an engine reading one cue has all it needs. `speed` is the pace the move was saved at;
    the times already include it.
    """
    points = {key: sound[key] for key in ("id", "files")} if sound else None
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "move_cues",
        "on": bool(on),
        "move": move,
        "model": model,
        "loop": bool(found["loop"]),
        "loop_seconds": found["seconds"] if found["loop"] else None,
        "seconds": found["seconds"],
        "speed": speed,
        "cues": [{"time_s": cue["time_s"], "foot": cue["foot"], "event": "footstep", "sound": points}
                 for cue in found["cues"]],
        "sound": sound,
        "found": {**(found.get("found") or {"how": HOW}),
                  "planted": {foot["foot"]: foot["planted"] for foot in found["feet"]}},
        "note": "Times are seconds from the start of the move as saved. Play it faster and the steps "
                "come sooner: divide each time by the speed-up. No audio is stored here.",
    }


def write_cues(move_glb: Path, record: dict[str, Any]) -> Path:
    path = cues_path(move_glb)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def read_cues(move_glb: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(cues_path(move_glb).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("kind") == "move_cues" else None
