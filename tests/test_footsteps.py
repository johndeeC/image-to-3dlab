"""Footstep marks (image_to_3dlab/footsteps.py): the shipped finder on made-up moves whose
steps are known, and the cue file it writes."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "scripts")]

import quadruped_gait

from image_to_3dlab import footsteps

FPS = 60
TALL = 1.8


def loop_times(seconds: float) -> np.ndarray:
    return np.linspace(0.0, seconds, int(round(seconds * FPS)) + 1)


def gait_heights(phases: dict[str, float], duty: float, seconds: float, lift: float = 0.1) -> dict[str, np.ndarray]:
    """Each foot's height through one loop, from the gait code the quadruped walk is built with."""
    times = loop_times(seconds)
    return {foot: np.array([quadruped_gait.foot_offset(t / seconds, phase, duty, 0.3, lift)[1] for t in times])
            for foot, phase in phases.items()}


def steps(found) -> list[tuple[float, str]]:
    return [(cue["time_s"], cue["foot"]) for cue in found["cues"]]


def test_two_feet_taking_turns_give_two_steps_a_loop():
    heights = gait_heights({"left": 0.0, "right": 0.5}, 0.6, 1.2)
    found = footsteps.find_steps(loop_times(1.2), heights, seconds=1.2, loop=True, body_height=TALL)
    assert [foot for _, foot in steps(found)] == ["left", "right"]
    assert steps(found)[0][0] == pytest.approx(0.0, abs=0.03) or steps(found)[0][0] == pytest.approx(1.2, abs=0.03)
    assert steps(found)[1][0] == pytest.approx(0.6, abs=0.03)
    left = found["feet"][0]
    assert left["steps"] == [{"time_s": steps(found)[0][0], "n": 1}]
    start, end = left["planted"][0]
    assert end - start == pytest.approx(0.6 * 1.2, abs=0.06)       # down for its duty share of the loop


def test_a_landing_on_the_loops_seam_is_one_step_not_two():
    heights = gait_heights({"left": 0.98, "right": 0.48}, 0.6, 1.0)
    found = footsteps.find_steps(loop_times(1.0), heights, seconds=1.0, loop=True, body_height=TALL)
    assert len(found["cues"]) == 2
    assert all(0.0 <= t < 1.0 for t, _ in steps(found))


@pytest.mark.parametrize("gait", sorted(quadruped_gait.GAITS))
def test_watching_a_four_legged_gait_agrees_with_its_own_maths(gait):
    spec, seconds = quadruped_gait.GAITS[gait], 1.0
    watched = footsteps.find_steps(loop_times(seconds), gait_heights(spec["phases"], spec["duty"], seconds),
                                   seconds=seconds, loop=True, body_height=TALL)
    known = footsteps.gait_steps(spec["phases"], spec["duty"], seconds)
    assert len(watched["cues"]) == len(known["cues"])
    by_foot = lambda found: {foot["foot"]: foot["steps"][0]["time_s"] for foot in found["feet"]}
    for foot, t in by_foot(known).items():
        gap = abs(by_foot(watched)[foot] - t)
        assert min(gap, seconds - gap) < 0.03
        # and the gait's own "is this foot down" agrees just after the landing
        assert quadruped_gait.is_planted(t / seconds + 0.01, spec["phases"][foot], spec["duty"])


def test_a_trot_is_two_beats_because_diagonal_feet_land_together():
    spec = quadruped_gait.GAITS["trot"]
    known = footsteps.gait_steps(spec["phases"], spec["duty"], 0.8)
    assert [cue["foot"] for cue in known["cues"]] == ["FL + BR", "FR + BL"]
    assert [cue["time_s"] for cue in known["cues"]] == [0.0, 0.4]
    assert [foot["steps"][0]["n"] for foot in known["feet"]] == [1, 1, 2, 2]


def test_a_walk_is_four_beats():
    spec = quadruped_gait.GAITS["walk"]
    assert [cue["foot"] for cue in footsteps.gait_steps(spec["phases"], spec["duty"], 1.0)["cues"]] == ["BL", "FL", "BR", "FR"]


def test_standing_still_has_no_steps():
    times = loop_times(4.0)
    sway = {"left": 0.004 * (1 + np.sin(times * 2)), "right": 0.004 * (1 + np.cos(times * 2))}
    found = footsteps.find_steps(times, sway, seconds=4.0, loop=True, body_height=TALL)
    assert found["cues"] == []
    assert found["feet"][0]["planted"] == [[0.0, 4.0]]
    assert footsteps.in_words(found).startswith("No steps found")


def test_a_jump_is_one_landing_for_both_feet():
    times = loop_times(1.5)
    air = np.where((times > 0.4) & (times < 1.0), 0.5 * np.sin(np.pi * (times - 0.4) / 0.6), 0.0)
    found = footsteps.find_steps(times, {"left": air, "right": air.copy()}, seconds=1.5, loop=False, body_height=TALL)
    assert [cue["foot"] for cue in found["cues"]] == ["left + right"]
    assert found["cues"][0]["time_s"] == pytest.approx(1.0, abs=0.05)
    assert found["feet"][0]["planted"][0][0] == 0.0               # stood on the floor to begin with: not a step
    assert footsteps.in_words(found) == "1 step in 1.5 s: left + right."


def test_a_shaky_floor_contact_is_still_one_step_each():
    rng = np.random.default_rng(3)
    clean = gait_heights({"left": 0.1, "right": 0.6}, 0.6, 1.0)
    noisy = {foot: np.abs(h + rng.normal(0, 0.006, len(h))) for foot, h in clean.items()}
    found = footsteps.find_steps(loop_times(1.0), noisy, seconds=1.0, loop=True, body_height=TALL)
    assert [foot for _, foot in steps(found)] == ["left", "right"]


def test_a_toe_brushing_the_floor_mid_swing_is_not_a_step():
    times = loop_times(1.0)
    height = np.where(times < 0.5, 0.0, 0.1 * np.sin(np.pi * (times - 0.5) / 0.5))
    height[(times > 0.74) & (times < 0.77)] = 0.0                 # two samples on the floor
    found = footsteps.find_steps(times, {"left": height}, seconds=1.0, loop=True, body_height=TALL)
    assert len(found["cues"]) == 1


def test_heel_down_with_the_toe_up_counts_as_down():
    ankle = np.array([0.30, 0.20, 0.11, 0.10, 0.10])
    toe = np.array([0.25, 0.20, 0.12, 0.05, 0.02])
    assert footsteps.foot_clearance(np.stack([ankle, toe], axis=1)).tolist() == pytest.approx([0.2, 0.1, 0.01, 0.0, 0.0])


def test_a_faster_move_has_the_same_steps_sooner():
    slow = footsteps.find_steps(loop_times(1.2), gait_heights({"left": 0.2, "right": 0.7}, 0.6, 1.2),
                                seconds=1.2, loop=True, body_height=TALL)
    fast = footsteps.find_steps(loop_times(0.6), gait_heights({"left": 0.2, "right": 0.7}, 0.6, 0.6),
                                seconds=0.6, loop=True, body_height=TALL)
    for a, b in zip(footsteps.at_speed(slow["cues"], 2.0), fast["cues"]):
        assert a["foot"] == b["foot"] and a["time_s"] == pytest.approx(b["time_s"], abs=0.02)
    assert fast["seconds"] == 0.6
    with pytest.raises(ValueError):
        footsteps.at_speed(slow["cues"], 0)


def test_the_words_say_how_many_and_which_feet():
    found = footsteps.gait_steps({"left": 0.0, "right": 0.5}, 0.6, 1.2)
    assert footsteps.in_words(found) == "2 steps in each 1.2 s loop: left, right."


def test_a_move_that_ends_where_it_began_is_a_loop():
    pose = np.zeros((10, 3, 3))
    assert footsteps.looks_like_loop(pose, TALL)
    pose[-1, 0, 2] = 1.0                                          # walked a metre away
    assert not footsteps.looks_like_loop(pose, TALL)


# ----------------------------------------------------------------------------- which bones are feet

def biped():
    parents = {"hips": None, "spine": "hips", "head": "spine",
               "thigh.a": "hips", "shin.a": "thigh.a", "ankle.a": "shin.a", "toe.a": "ankle.a",
               "thigh.b": "hips", "shin.b": "thigh.b", "ankle.b": "shin.b", "toe.b": "ankle.b"}
    rest = {"hips": (0, 1.0, 0), "spine": (0, 1.3, 0), "head": (0, 1.7, 0),
            "thigh.a": (0.1, 0.95, 0), "shin.a": (0.1, 0.5, 0.02), "ankle.a": (0.1, 0.08, 0), "toe.a": (0.1, 0.02, 0.12),
            "thigh.b": (-0.1, 0.95, 0), "shin.b": (-0.1, 0.5, 0.02), "ankle.b": (-0.1, 0.08, 0), "toe.b": (-0.1, 0.02, 0.12)}
    return parents, {name: np.array(p, dtype=float) for name, p in rest.items()}


def test_the_lowest_chain_ends_are_feet_whatever_the_bones_are_called():
    parents, rest = biped()
    assert footsteps.lowest_ends(parents, rest) == {"left": ["ankle.a", "toe.a"], "right": ["ankle.b", "toe.b"]}


def test_four_feet_are_named_by_where_they_stand():
    parents = {"body": None, "head": "body"}
    rest = {"body": np.array([0, 0.6, 0.0]), "head": np.array([0, 0.8, 0.5])}
    for name, x, z in (("a", 0.1, 0.4), ("b", -0.1, 0.4), ("c", 0.1, -0.4), ("d", -0.1, -0.4)):
        parents[f"leg.{name}"], parents[f"paw.{name}"] = "body", f"leg.{name}"
        rest[f"leg.{name}"], rest[f"paw.{name}"] = np.array([x, 0.5, z]), np.array([x, 0.0, z])
    assert footsteps.lowest_ends(parents, rest) == {
        "back left": ["paw.c"], "back right": ["paw.d"], "front left": ["paw.a"], "front right": ["paw.b"]}


def test_steps_are_read_from_bone_tracks():
    parents, rest = biped()
    names = list(parents)
    times = loop_times(1.0)
    heights = gait_heights({"a": 0.0, "b": 0.5}, 0.6, 1.0)
    positions = np.tile(np.stack([rest[n] for n in names]), (len(times), 1, 1))
    for side in ("a", "b"):
        for bone in (f"ankle.{side}", f"toe.{side}"):
            positions[:, names.index(bone), 1] += heights[side]
    tracks = {"names": names, "parents": parents, "rest": np.stack([rest[n] for n in names]), "times": times,
              "positions": positions, "seconds": 1.0}
    found = footsteps.steps_in_tracks(tracks)
    assert found["loop"] is True and len(found["cues"]) == 2
    assert set(found["found"]["feet"]) == {"left", "right"}
    assert footsteps.steps_in_tracks(tracks, loop=False)["loop"] is False


def test_a_skeleton_with_no_feet_is_refused():
    with pytest.raises(LookupError):
        footsteps.steps_in_tracks({"names": [], "parents": {}, "rest": np.zeros((0, 3)), "times": np.zeros(2),
                                   "positions": np.zeros((2, 0, 3)), "seconds": 1.0})


# ----------------------------------------------------------------------------- the cue file

SOUND = {"id": "boots_set__20261120-093300", "kind": "sound_set", "name": "Boots",
         "files": ["../../sounds/boots_set__20261120-093300/boots_01.wav"],
         "license": {"name": "Apache-2.0"}, "record": "../../sounds/boots_set__20261120-093300/set.provenance.json"}


def test_the_cue_file_names_the_move_the_times_and_the_sound_and_holds_no_audio(tmp_path):
    found = footsteps.gait_steps({"left": 0.0, "right": 0.5}, 0.6, 1.2)
    record = footsteps.cue_record(found, move="walk", model="knight_walk.glb", speed=1.25, sound=SOUND)
    assert record["schema_version"] == 1 and record["kind"] == "move_cues" and record["on"] is True
    assert (record["move"], record["loop_seconds"], record["speed"]) == ("walk", 1.2, 1.25)
    assert record["cues"] == [
        {"time_s": 0.0, "foot": "left", "event": "footstep", "sound": {"id": SOUND["id"], "files": SOUND["files"]}},
        {"time_s": 0.6, "foot": "right", "event": "footstep", "sound": {"id": SOUND["id"], "files": SOUND["files"]}}]
    assert record["sound"]["record"].endswith("set.provenance.json")     # points at the sound's licence record
    assert record["found"]["planted"]["left"] == [[0.0, 0.72]]
    glb = tmp_path / "knight_walk.glb"
    path = footsteps.write_cues(glb, record)
    assert path == tmp_path / "knight_walk.cues.json"
    assert json.loads(path.read_text()) == record == footsteps.read_cues(glb)
    assert path.stat().st_size < 4000


def test_a_cue_file_without_a_sound_and_a_missing_one(tmp_path):
    found = footsteps.gait_steps({"left": 0.0}, 0.5, 1.0)
    record = footsteps.cue_record(found, move="walk", model="a.glb", on=False)
    assert record["on"] is False and record["sound"] is None and record["cues"][0]["sound"] is None
    assert footsteps.read_cues(tmp_path / "nothing.glb") is None
    (tmp_path / "bad.cues.json").write_text("{not json")
    assert footsteps.read_cues(tmp_path / "bad.glb") is None


def test_gait_steps_refuse_nonsense():
    with pytest.raises(ValueError):
        footsteps.gait_steps({"left": 0.0}, 1.5, 1.0)
    assert math.isclose(footsteps.gait_steps({"left": 1.25}, 0.5, 2.0)["cues"][0]["time_s"], 0.5)
