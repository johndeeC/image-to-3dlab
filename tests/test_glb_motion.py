"""Reading a move out of a GLB as numbers (image_to_3dlab/glb_motion.py), on a tiny GLB built
here with the shipped writer: a hip, a leg that swings and a foot that is lifted."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from image_to_3dlab import footsteps, glb_motion
from image_to_3dlab.glb_turn import build_glb, parse_glb


def tiny_glb(*, how: str = "LINEAR", keys=(1.0, 2.0, 3.0), lifts=(0.0, 0.5, 0.0), move: bool = True) -> bytes:
    """holder (moved 10 along x) > hips (1 up) > leg (1 down) > foot (keyed up and down)."""
    blob = bytearray()
    views, accessors = [], []

    def add(values: np.ndarray, kind: str) -> int:
        data = np.asarray(values, dtype="<f4").tobytes()
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        blob.extend(data)
        accessors.append({"bufferView": len(views) - 1, "componentType": 5126, "count": len(values), "type": kind})
        return len(accessors) - 1

    times = add(np.array(keys), "SCALAR")
    places = add(np.array([[0.0, lift, 0.2] for lift in lifts]), "VEC3")
    half = np.sqrt(0.5)
    turns = add(np.array([[0, 0, 0, 1], [0, 0, half, half], [0, 0, 0, 1]]), "VEC4")   # leg: a quarter turn about z and back
    document = {
        "asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "holder", "translation": [10, 0, 0], "children": [1]},
                  {"name": "hips", "translation": [0, 1, 0], "children": [2]},
                  {"name": "leg", "translation": [0, -1, 0], "children": [3]},
                  {"name": "foot", "translation": [0, 0, 0.2]}],
        "skins": [{"joints": [1, 2, 3]}],
        "buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": accessors,
    }
    if move:
        document["animations"] = [{
            "samplers": [{"input": times, "output": places, "interpolation": how},
                         {"input": times, "output": turns, "interpolation": "LINEAR"}],
            "channels": [{"sampler": 0, "target": {"node": 3, "path": "translation"}},
                         {"sampler": 1, "target": {"node": 2, "path": "rotation"}}]}]
    return build_glb(document, bytes(blob))


def tracks(**how):
    return glb_motion.joint_tracks(*parse_glb(tiny_glb(**how)))


def test_bones_are_followed_through_the_move_in_world_space():
    found = tracks()
    assert found["names"] == ["hips", "leg", "foot"]
    assert found["parents"] == {"hips": None, "leg": "hips", "foot": "leg"}
    assert found["seconds"] == pytest.approx(2.0)                 # the keys run from 1 s to 3 s
    assert found["times"][0] == 0.0 and found["times"][-1] == pytest.approx(2.0)
    assert len(found["times"]) == 2 * glb_motion.SAMPLE_FPS + 1
    assert found["rest"].tolist() == [[10, 1, 0], [10, 0, 0], [10, 0, pytest.approx(0.2)]]
    foot = found["positions"][:, 2]
    assert foot[0].tolist() == pytest.approx([10, 0, 0.2])        # the holder's shift is counted
    # halfway: the leg has turned a quarter about z, so the foot's own "up 0.5" points along -x
    assert foot[glb_motion.SAMPLE_FPS].tolist() == pytest.approx([9.5, 0, 0.2], abs=1e-6)
    assert foot[-1].tolist() == pytest.approx([10, 0, 0.2])


def test_step_keys_hold_until_the_next_key():
    held = tracks(how="STEP")["positions"][:, 2]
    assert held[glb_motion.SAMPLE_FPS - 1].tolist() == pytest.approx([10, 0, 0.2], abs=1e-6)


def test_a_file_with_no_move_or_no_skeleton_is_refused():
    with pytest.raises(LookupError, match="no move"):
        tracks(move=False)
    document, binary = parse_glb(tiny_glb())
    document.pop("skins")
    with pytest.raises(LookupError, match="no skeleton"):
        glb_motion.joint_tracks(document, binary)
    with pytest.raises(ValueError):
        footsteps.steps_in_glb(b"not a glb at all")
    with pytest.raises(ValueError):
        footsteps.steps_in_glb(b"")


def test_byte_strides_and_packed_turns_are_read():
    document, binary = parse_glb(tiny_glb())
    packed = np.round(glb_motion._accessor(document, binary, 2) * 32767).astype("<i2").tobytes()
    document["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(packed)})
    document["accessors"][2] = {"bufferView": len(document["bufferViews"]) - 1, "componentType": 5122,
                                "normalized": True, "count": 3, "type": "VEC4"}
    again = glb_motion.joint_tracks(document, binary + packed)
    assert again["positions"][glb_motion.SAMPLE_FPS, 2].tolist() == pytest.approx([9.5, 0, 0.2], abs=1e-3)
