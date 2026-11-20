"""Where every bone is, moment by moment, in an animated GLB. Numbers only: no Blender, no
three.js, so the same answer comes out in the studio, in an agent tool and in a test.

`joint_tracks` plays the file's first move at a steady rate and returns each skeleton joint's
world position at each moment, plus the rest pose and who hangs from whom. glTF is Y-up, so
height is `[..., 1]`. What reads those tracks (which feet touch the floor, and when) lives in
`footsteps.py`.
"""

from __future__ import annotations

from typing import Any

import numpy as np

SAMPLE_FPS = 60
_COMPONENTS = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_WIDTH = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}


def _accessor(document: dict, binary: bytes, index: int) -> np.ndarray:
    """An accessor as a (count, width) float array, strides and normalised integers honoured."""
    accessor = document["accessors"][index]
    if "sparse" in accessor or "bufferView" not in accessor or accessor.get("type") not in _WIDTH:
        raise ValueError("this move stores its keys in a way that cannot be read here")
    view = document["bufferViews"][accessor["bufferView"]]
    dtype = np.dtype(_COMPONENTS[accessor["componentType"]])
    width = _WIDTH[accessor["type"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride") or width * dtype.itemsize
    values = np.ndarray((accessor["count"], width), dtype=dtype, buffer=binary, offset=start,
                        strides=(stride, dtype.itemsize)).astype(np.float64)
    if accessor.get("normalized") and dtype.kind in "iu":
        values = np.maximum(values / np.iinfo(dtype).max, -1.0)
    return values


def _at(times: np.ndarray, keys: np.ndarray, values: np.ndarray, how: str, turn: bool) -> np.ndarray:
    """The sampler's value at each of `times`. Outside the keys it holds the nearest one."""
    if how == "CUBICSPLINE":          # (in-tangent, value, out-tangent) per key: the values are enough here
        values = values.reshape(len(keys), 3, -1)[:, 1]
    if len(keys) == 1:
        return np.repeat(values[:1], len(times), axis=0)
    after = np.clip(np.searchsorted(keys, times, side="right"), 1, len(keys) - 1)
    before = after - 1
    if how == "STEP":
        return values[np.where(times >= keys[after], after, before)]
    span = keys[after] - keys[before]
    share = np.clip((times - keys[before]) / np.where(span > 0, span, 1.0), 0.0, 1.0)[:, None]
    a, b = values[before], values[after]
    if turn:                          # the short way round between two turns
        b = np.where((a * b).sum(axis=1, keepdims=True) < 0, -b, b)
    out = a + (b - a) * share
    if turn:
        out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)
    return out


def _turns(q: np.ndarray) -> np.ndarray:
    """(n, 4) x-y-z-w quaternions as (n, 3, 3) matrices."""
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    return np.stack([
        1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
        2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
        2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y),
    ], axis=1).reshape(-1, 3, 3)


def _locals(node: dict, moved: dict[str, np.ndarray], count: int) -> np.ndarray:
    """One node's own transform at each moment, (count, 4, 4)."""
    out = np.tile(np.eye(4), (count, 1, 1))
    if "matrix" in node and not moved:
        out[:] = np.array(node["matrix"], dtype=float).reshape(4, 4).T   # glTF is column-major
        return out
    place = moved.get("translation", np.tile(node.get("translation", [0, 0, 0]), (count, 1)))
    turn = moved.get("rotation", np.tile(node.get("rotation", [0, 0, 0, 1]), (count, 1)))
    size = moved.get("scale", np.tile(node.get("scale", [1, 1, 1]), (count, 1)))
    out[:, :3, :3] = _turns(np.asarray(turn, dtype=float)) * np.asarray(size, dtype=float)[:, None, :]
    out[:, :3, 3] = place
    return out


def _world(document: dict, local: dict[int, np.ndarray], wanted: list[int], count: int) -> dict[int, np.ndarray]:
    parent = {child: i for i, node in enumerate(document["nodes"]) for child in node.get("children", [])}
    world: dict[int, np.ndarray] = {}

    def of(i: int) -> np.ndarray:
        if i not in world:
            mine = local[i] if i in local else _locals(document["nodes"][i], {}, count)
            world[i] = of(parent[i]) @ mine if i in parent else mine
        return world[i]

    return {i: of(i) for i in wanted}


def joint_tracks(document: dict, binary: bytes, *, fps: int = SAMPLE_FPS, animation: int = 0) -> dict[str, Any]:
    """The first skin's joints through the file's move, sampled `fps` times a second.

    Returns `names`, `parents` ({joint: parent joint or None}), `rest` ((joints, 3), the pose
    the file stands in with no move playing), `times` (seconds from the move's start),
    `positions` ((moments, joints, 3), world space) and `seconds` (how long the move is).
    Raises LookupError when the file has no skeleton or no move.
    """
    nodes = document.get("nodes") or []
    skins = document.get("skins") or []
    if not skins or not skins[0].get("joints"):
        raise LookupError("this model has no skeleton")
    moves = document.get("animations") or []
    if animation >= len(moves) or not moves[animation].get("channels"):
        raise LookupError("this model has no move in it")
    move = moves[animation]
    keys = {i: _accessor(document, binary, s["input"])[:, 0] for i, s in enumerate(move["samplers"])}
    start = min(float(k[0]) for k in keys.values())
    end = max(float(k[-1]) for k in keys.values())
    seconds = end - start
    count = max(2, int(round(seconds * fps)) + 1)
    times = np.linspace(0.0, seconds, count)

    moved: dict[int, dict[str, np.ndarray]] = {}
    for channel in move["channels"]:
        target = channel.get("target") or {}
        path, node = target.get("path"), target.get("node")
        if node is None or path not in ("translation", "rotation", "scale"):
            continue
        sampler = move["samplers"][channel["sampler"]]
        values = _accessor(document, binary, sampler["output"])
        moved.setdefault(node, {})[path] = _at(times + start, keys[channel["sampler"]], values,
                                               sampler.get("interpolation", "LINEAR"), path == "rotation")
    joints = list(skins[0]["joints"])
    playing = {i: _locals(nodes[i], channels, count) for i, channels in moved.items()}
    world = _world(document, playing, joints, count)
    still = _world(document, {}, joints, 1)

    names = [nodes[i].get("name") or f"joint{i}" for i in joints]
    name_of = dict(zip(joints, names))
    parent = {child: i for i, node in enumerate(nodes) for child in node.get("children", [])}

    def joint_parent(i: int) -> str | None:
        p = parent.get(i)
        while p is not None and p not in name_of:
            p = parent.get(p)
        return name_of.get(p)

    return {
        "names": names,
        "parents": {name_of[i]: joint_parent(i) for i in joints},
        "rest": np.stack([still[i][0, :3, 3] for i in joints]),
        "times": times,
        "positions": np.stack([world[i][:, :3, 3] for i in joints], axis=1),
        "seconds": seconds,
    }
