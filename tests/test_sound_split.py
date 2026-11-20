"""Split a description into layers (image_to_3dlab/sound_split.py): the real rule table."""

from __future__ import annotations

import pytest

from image_to_3dlab import sound_post, sound_split


def said(description):
    return [(layer["role"], layer["describe"], layer["start_ms"]) for layer in sound_split.split(description)["layers"]]


def test_an_arrow_is_a_whoosh_then_one_thud():
    out = sound_split.split("Arrow hits a wooden target")
    assert out["rule"] == "thrown-hit"
    assert said("Arrow hits a wooden target") == [
        ("lead-in", "arrow whoosh past, fast air, dry", 0),
        ("hit", "arrow thudding into wood, one hit, dry", 250)]
    assert said("a spear thuds into a stone wall")[1][1] == "spear thudding into stone, one hit, dry"


def test_a_door_is_a_slam_then_a_latch():
    assert said("door slams") == [("hit", "heavy wooden door slam, one hit", 0), ("tail", "metal latch click", 180)]
    assert said("slamming a metal door shut")[0][1] == "heavy metal door slam, one hit"
    assert sound_split.split("a door creaks")["rule"] is None           # not a slam: nothing to split


def test_a_footstep_with_an_echo_is_a_dry_step_then_the_echo():
    assert said("giant footstep with a cave echo") == [
        ("hit", "giant footstep on stone, one heavy step, dry", 0),
        ("tail", "low echo dying away in a cave", 120)]
    assert sound_split.split("footstep on gravel")["rule"] is None


@pytest.mark.parametrize("description, rule, count", [
    ("sword strikes a wooden shield", "blade-hit", 3),
    ("a big explosion", "explosion", 3),
    ("glass bottle smashes", "glass", 2),
    ("pistol gunshot", "gunshot", 2),
    ("a punch to the face", "punch", 2),
    ("casting a fireball spell", "spell", 3),
    ("rock dropped into the water", "splash", 2),
    ("a bell with a long echo in a cathedral", "echo", 2),
    ("a click, then a whirr, followed by a beep", "then", 3),
])
def test_every_rule_gives_two_or_three_layers_in_order(description, rule, count):
    out = sound_split.split(description)
    assert out["rule"] == rule and len(out["layers"]) == count
    starts = [layer["start_ms"] for layer in out["layers"]]
    assert starts == sorted(starts) and starts[0] == 0
    low, high = sound_post.LAYER_LIMITS["start_ms"]
    assert all(low <= start <= high for start in starts)
    assert all(layer["describe"] and layer["label"] == sound_split.ROLES[layer["role"]] for layer in out["layers"])


def test_the_echo_rule_keeps_the_thing_and_names_the_room():
    assert said("a bell with a long echo in a cathedral") == [
        ("hit", "a bell, dry", 0), ("tail", "echo dying away in a big hall", 120)]


def test_with_no_rule_the_description_comes_back_as_one_layer_unchanged():
    out = sound_split.split("  Coin   pickup, bright ")
    assert out == {"rule": None, "layers": [{"role": "whole", "label": "Whole sound",
                                             "describe": "Coin pickup, bright", "start_ms": 0}]}


def test_every_rule_is_reached_and_has_an_id_of_its_own():
    ids = [rule for rule, _, _ in sound_split.RULES]
    assert len(ids) == len(set(ids)) and 2 <= len(ids) <= 20


def test_nothing_typed_or_far_too_much_is_refused():
    with pytest.raises(ValueError, match="describe the sound first"):
        sound_split.split("   ")
    with pytest.raises(ValueError, match="500"):
        sound_split.split("x" * 501)
