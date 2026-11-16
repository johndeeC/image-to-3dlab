"""The kit pane's logic (viewer/studio/kit.js), run under Node against the shipped file."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required to execute browser ES modules")


def _run(body: str):
    url = (REPO / "viewer" / "studio" / "kit.js").as_uri()
    program = f"import * as m from {json.dumps(url)};\n{body}"
    result = subprocess.run([NODE, "--input-type=module", "--eval", program],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def _asset(id_, **kw):
    base = {"id": id_, "family": id_, "name": id_, "kind": "character", "stage": "picture", "picture": None,
            "prompt": None, "model": None, "finished": None, "rigged": None, "clips": [], "props": [], "updated": 0}
    return {**base, **kw}


# a knight: v1 rigged, v2 a second Finish of the same model, v3 a second 3D model; and a second picture
KNIGHT = _asset("k1", family="k1", picture="images/k.png", prompt="A rune knight, A-pose, plain background",
                model="a/k.glb", finished="finish/k_20k.glb", rigged="animate/k_rigged.glb", stage="rigged", updated=5)
KNIGHT_FIN2 = {**KNIGHT, "id": "k2", "finished": "finish/k_40k.glb", "rigged": None, "stage": "finished", "updated": 6}
KNIGHT_MODEL2 = {**KNIGHT, "id": "k3", "model": "a/k2.glb", "finished": None, "rigged": None, "stage": "model", "updated": 7}
KNIGHT_PIC2 = _asset("k4", picture="images/k2.png", prompt=KNIGHT["prompt"], updated=8)
STRANGER = _asset("s1", picture="images/s.png", prompt="a fox")
SHEET = _asset("p1", kind="prop set", stage="finished", picture="images/p.png", prompt="gear", model="a/p.glb",
               props=[{"name": "prop_01", "file": "props/p/prop_01_LOD0.glb"}, {"name": "shield-big", "file": "props/p/b.glb"}])
SWORD = _asset("p2", kind="prop", stage="finished", picture="images/s2.png", model="a/s.glb", finished="finish/s.glb", name="sword")
ALL = [KNIGHT, KNIGHT_FIN2, KNIGHT_MODEL2, KNIGHT_PIC2, STRANGER, SHEET, SWORD]
KIT = {"id": "kit-00000001", "name": "Knight kit", "character": "k1", "gear": ["p1", "p2", "ffffffffffff"]}


def test_make_gear_starts_from_the_characters_words_without_the_studios_wording():
    recipes = {"goals": {"character": {"wording": "A-pose, plain background"}}}
    out = _run(f"console.log(JSON.stringify([m.gearPrompt({json.dumps(KNIGHT)}, {json.dumps(recipes)}),"
               f" m.gearPrompt({{name: 'orc'}}, null)]))")
    assert out[0].startswith("Nine pieces of gear for a rune knight, in the same art style: a sword, a shield")
    assert out[1].startswith("Nine pieces of gear for orc, ")


def test_each_member_is_a_row_of_its_own_steps():
    lanes = _run(f"console.log(JSON.stringify(m.kitLanes({json.dumps(KIT)}, {json.dumps(ALL)})))")
    assert [(l["role"], l["id"], l["missing"]) for l in lanes] == [
        ("character", "k1", False), ("gear", "p1", False), ("gear", "p2", False), ("gear", "ffffffffffff", True)]
    knight, sheet, sword = lanes[0]["nodes"], lanes[1]["nodes"], lanes[2]["nodes"]
    assert [(n["id"], n["state"]) for n in knight] == [
        ("picture", "done"), ("model", "done"), ("finished", "done"), ("rigged", "done"), ("animated", "now")]
    assert [(n["label"], n["state"]) for n in sheet] == [("Picture", "done"), ("3D model", "done"), ("Split", "done")]
    assert [n["label"] for n in sword] == ["Picture", "3D model", "Finish"]
    assert lanes[3]["nodes"] == []


def test_options_are_the_tries_of_that_step_own_one_first():
    out = _run(f"""const all = {json.dumps(ALL)}, k = all[0];
      console.log(JSON.stringify(['picture', 'model', 'finished', 'rigged', 'animated'].map((s) => m.stepOptions(k, all, s).map((a) => a.id))));""")
    # pictures: same prompt; models: same picture; finishes: same model; rigs: same finish
    assert out == [["k1", "k4"], ["k1", "k3"], ["k1", "k2"], ["k1"], []]


def test_an_option_stands_for_the_version_that_went_furthest():
    # from v2's point of view, the 20k Finish is best shown by v1, which went on to a rig
    out = _run(f"""const all = {json.dumps(ALL)};
      console.log(JSON.stringify(m.stepOptions(all[1], all, 'finished').map((a) => a.id)));""")
    assert out == ["k2", "k1"]


def test_the_props_grid_shows_every_split_prop_at_once():
    out = _run(f"console.log(JSON.stringify(m.kitProps({json.dumps(KIT)}, {json.dumps(ALL)})))")
    assert [(p["name"], p["setId"], p["index"]) for p in out] == [
        ("prop 01", "p1", 0), ("shield big", "p1", 1), ("sword", "p2", -1)]


def test_node_buttons_follow_the_step_state():
    out = _run(f"""const k = {json.dumps(KNIGHT)}, up = {{...k, prompt: null}};
      console.log(JSON.stringify([
        m.nodeActions({{id: 'model', state: 'done'}}, k), m.nodeActions({{id: 'model', state: 'now'}}, k),
        m.nodeActions({{id: 'model', state: 'later'}}, k), m.nodeActions({{id: 'picture', state: 'done'}}, up),
        m.nodeActions({{id: 'animated', state: 'done'}}, k), m.nodeActions({{id: 'animated', state: 'now'}}, k)]));""")
    assert out == [["try"], ["make"], [], [], [], ["make"]]


def test_redo_and_times_n_make_that_many_fresh_tries():
    out = _run(f"""const k = {json.dumps(KNIGHT)}, sheet = {json.dumps(SHEET)};
      const models = m.tryPlans('model', k, 3, {{engine: 'pixal3d'}});
      console.log(JSON.stringify({{
        models: models.map((p) => [p.steps, p.engine, p.made.picture]),
        seeds: new Set(models.map((p) => p.modelSeed)).size,
        pictures: m.tryPlans('picture', k, 2).map((p) => [p.steps, p.prompt, typeof p.pictureSettings.seed]),
        finish: m.tryPlans('finished', k, 1)[0].finishSettings.faces,
        split: m.tryPlans('finished', sheet, 1)[0].steps,
        rig: m.tryPlans('rigged', k, 1)[0].made,
        capped: m.tryPlans('rigged', k, 9).length,
        noEngine: m.tryPlans('model', k, 2, {{}}).length,
        noMove: m.tryPlans('animated', k, 1, {{}}).length,
        move: m.tryPlans('animated', k, 1, {{move: 'idle'}})[0].firstMove,
      }}));""")
    assert out["models"] == [[["model"], "pixal3d", "images/k.png"]] * 3
    assert out["seeds"] == 3                       # each try is a different one
    assert out["pictures"] == [[["picture"], KNIGHT["prompt"], "number"]] * 2
    assert out["finish"] == 20000
    assert out["split"] == ["split"]
    assert out["rig"] == {"picture": "images/k.png", "finished": "finish/k_20k.glb"}
    assert out["capped"] == 4 and out["noEngine"] == 0 and out["noMove"] == 0 and out["move"] == "idle"


def test_kits_with_finds_an_asset_as_character_or_gear():
    out = _run(f"""const kits = [{json.dumps(KIT)}];
      console.log(JSON.stringify([m.kitsWith(kits, 'k1').length, m.kitsWith(kits, 'p2').length, m.kitsWith(kits, 'zz').length, m.kitsWith(null, 'k1').length]));""")
    assert out == [1, 1, 0, 0]
