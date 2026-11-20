"""The Footsteps card's logic (viewer/studio/footsteps.js), run under Node against the shipped
file, and the page wiring read from the files that ship."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUDIO = REPO / "viewer" / "studio"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is required to execute browser ES modules")

SET = {"id": "boots_set__20261120-093300", "kind": "sound_set", "name": "Boots on gravel", "count": 3,
       "file": "sounds/s/boots_01.wav", "files": ["sounds/s/boots_01.wav", "sounds/s/boots_02.wav", "sounds/s/boots_03.wav"],
       "play_order": [2, 0, 1, 0, 2, 1]}
ONE = {"id": "boots__20261120-091306", "kind": "sound", "name": "Boots on stone", "file": "sounds/o/sound.wav"}
WALK = {"move": "walk", "seconds": 1.2, "loop": True,
        "cues": [{"time_s": 0.02, "foot": "left"}, {"time_s": 0.62, "foot": "right"}],
        "feet": [{"foot": "left", "planted": [[0.0, 0.3]], "steps": [{"time_s": 0.02, "n": 1}]},
                 {"foot": "right", "planted": [[0.6, 1.5]], "steps": [{"time_s": 0.62, "n": 2}]}]}


def _run(body: str):
    url = (STUDIO / "footsteps.js").as_uri()
    program = (f"import * as m from {json.dumps(url)};\nconst SET = {json.dumps(SET)}, ONE = {json.dumps(ONE)}, "
               f"WALK = {json.dumps(WALK)};\n{body}")
    result = subprocess.run([NODE, "--input-type=module", "--eval", program], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


@needs_node
def test_sets_come_first_in_the_picker_and_say_how_many_variations():
    out = _run("console.log(JSON.stringify([m.soundChoices([ONE, SET]), m.soundChoices([ONE, SET], ONE.id), m.soundChoices([])]))")
    assert out[0] == [{"id": SET["id"], "label": "Boots on gravel · 3 variations", "selected": True},
                      {"id": ONE["id"], "label": "Boots on stone · one sound", "selected": False}]
    assert [c["selected"] for c in out[1]] == [False, True] and out[2] == []


@needs_node
def test_the_hint_recommends_a_set_and_says_so_plainly_when_nothing_is_kept():
    out = _run("console.log(JSON.stringify([m.soundHint([ONE, SET], SET.id), m.soundHint([ONE, SET], ONE.id), m.soundHint([], null)]))")
    assert out[0] == "A set with variations sounds best: no two steps in a row the same."
    assert "Make variations" in out[1] and out[2].startswith("No kept sounds yet.")


@needs_node
def test_the_timeline_has_a_row_a_foot_with_spans_and_numbered_dots():
    out = _run("console.log(JSON.stringify([m.timelineRows(WALK), m.axisTicks(1.2), m.foundTitle(WALK), m.timelineRows({}), m.foundTitle({ move: 'run_b', label: 'Run' })]))")
    left, right = out[0]
    assert left == {"name": "Left foot", "planted": [{"left": "0%", "width": "25%"}], "steps": [{"left": "1.67%", "n": 1}]}
    # a span that runs past the end of the loop carries on from the start
    assert right["planted"] == [{"left": "50%", "width": "50%"}, {"left": "0%", "width": "25%"}]
    assert right["steps"] == [{"left": "51.67%", "n": 2}]
    assert out[1] == ["0 s", "0.3", "0.6", "0.9", "1.2 s"] and out[2] == "Found in the walk" and out[3] == [] and out[4] == "Found in the run"


@needs_node
def test_feet_are_named_for_a_person():
    out = _run("console.log(JSON.stringify(['left', 'right', 'front left', 'foot 2', 'left + right'].map(m.footName)))")
    assert out == ["Left foot", "Right foot", "Front left", "Foot 2", "Left + right"]


@needs_node
def test_a_step_sound_is_due_once_as_the_clock_passes_it_and_across_the_loops_seam():
    out = _run("""const c = WALK.cues, names = (list) => list.map((cue) => cue.foot);
      console.log(JSON.stringify([
        names(m.dueCues(c, null, 0.03)),            // the first look: a step at the very start is heard
        names(m.dueCues(c, 0.03, 0.5)),             // nothing in between
        names(m.dueCues(c, 0.5, 0.7)),
        names(m.dueCues(c, 0.7, 0.71)),             // not twice
        names(m.dueCues(c, 1.19, 0.05)),            // the clock wrapped: the step just after the seam
        names(m.dueCues(c, 1.19, 0.05, { loop: false })),
        names(m.dueCues(c, 0.04, 0.06, { startsAt: 0.0333 })),   // the file's first key is not at zero
      ]))""")
    assert out == [["left"], [], ["right"], [], ["left"], [], ["left"]]


@needs_node
def test_a_set_plays_in_a_games_order_and_one_sound_plays_itself():
    out = _run("""let played = 0; const files = [];
      for (let i = 0; i < 7; i += 1) { const next = m.nextStepFile(SET, played); played = next.played; files.push(next.file); }
      console.log(JSON.stringify([files, m.nextStepFile(ONE, 4), m.nextStepFile(null, 2)]))""")
    order = [SET["files"][i] for i in SET["play_order"]]
    assert out[0] == order + order[:1]
    assert all(a != b for a, b in zip(out[0], out[0][1:]))          # never the same one twice running
    assert out[1] == {"file": ONE["file"], "played": 5} and out[2] == {"file": None, "played": 2}


@needs_node
def test_what_is_sent_and_said():
    out = _run("""console.log(JSON.stringify([m.saveRequest('animate/r/k_walk.glb', SET.id), m.saveRequest('f.glb', null, false),
      m.stepsProblem(null), m.stepsProblem({ cues: [] }), m.stepsProblem(WALK),
      m.savedLine({ cues_file: 'animate/r/k_walk.cues.json' }), m.savedLine({})]))""")
    assert out[0] == {"file": "animate/r/k_walk.glb", "on": True, "sound_id": SET["id"]}
    assert out[1] == {"file": "f.glb", "on": False}
    assert out[2] == "Looking for the steps…" and out[3] == "There is nothing to mark on this move." and out[4] is None
    assert out[5] == "Saved k_walk.cues.json, beside the move." and out[6] == ""


# ----------------------------------------------------------------------------- the card and the page

def _pane(body: str):
    url = (STUDIO / "footsteps-pane.js").as_uri()
    program = (f"import * as m from {json.dumps(url)};\nconst SET = {json.dumps(SET)}, ONE = {json.dumps(ONE)}, "
               f"WALK = {json.dumps({**WALK, 'words': '2 steps in each 1.2 s loop: left, right.'})};\n{body}")
    result = subprocess.run([NODE, "--input-type=module", "--eval", program], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


@needs_node
def test_the_card_says_what_the_design_says_in_plain_words():
    html = _pane("console.log(JSON.stringify(m.cardHtml(WALK, [ONE, SET], null, true, null)))")
    for words in ("Footsteps on this move", "Marks each step so your game plays a sound there.", "Step sound",
                  "Boots on gravel · 3 variations", "A set with variations sounds best: no two steps in a row the same.",
                  "Found in the walk", "2 steps in each 1.2 s loop: left, right.", "Play with sound", "Save step marks",
                  "Shaded: foot on the ground. Dots: where a step sound plays.", "Left foot", "Right foot"):
        assert words in html, words
    assert "Drag a dot" not in html                      # dots cannot be dragged, so the card does not say they can
    assert "data-steps-on checked" in html and html.count("data-step-n=") == 2
    assert "disabled" not in html
    for jargon in ("cue", "JSON", "hysteresis", "keyframe", "sample"):
        assert jargon not in html, jargon


@needs_node
def test_a_move_with_no_steps_cannot_be_played_or_saved_and_says_why():
    html = _pane("""const still = { move: 'idle', seconds: 3, loop: true, cues: [], feet: [],
        words: 'No steps found: the feet stay on the floor in this move.' };
      console.log(JSON.stringify([m.cardHtml(still, [SET], null, true, 'There is nothing to mark on this move.'),
                                  m.cardHtml(null, [], null, true, 'there is no such move')]))""")
    assert "No steps found: the feet stay on the floor in this move." in html[0]
    assert html[0].count(" disabled") == 2 and "Found in the idle" in html[0]
    assert "there is no such move" in html[1] and "No sound, just the marks" in html[1]


@needs_node
def test_names_from_files_cannot_inject_markup():
    html = _pane("""const bad = { ...SET, name: '<img src=x onerror=1>' };
      console.log(JSON.stringify(m.cardHtml({ ...WALK, move: '<b>x</b>' }, [bad], null, true, null)))""")
    assert "<img src=x" not in html and "<b>x</b>" not in html


def test_the_page_puts_the_card_under_the_move_on_show_and_plays_through_the_viewer():
    app = (STUDIO / "app.js").read_text(encoding="utf-8")
    viewer = (STUDIO / "viewer.js").read_text(encoding="utf-8")
    pane = (STUDIO / "footsteps-pane.js").read_text(encoding="utf-8")
    assert "from './footsteps-pane.js'" in app and "mountFootsteps($('steps'), stepsCtx)" in app
    assert "showFootsteps();" in app.split("function renderSteps(asset)")[1].split("\n}\n")[0]   # the list is redrawn there
    assert "onFrame, replay, currentClip }" in viewer and "frameListener?.(time, duration, !!player?.playing)" in viewer
    assert "onClipChange?.(null)" in viewer                       # the card goes when no move is on show
    assert "/api/footsteps?file=${encodeURIComponent(clip.file)}" in pane and "call('/api/footsteps', saveRequest(" in pane
    assert "nextStepFile(sound(), played)" in pane                # a set goes round in a game's order
    css = (STUDIO / "studio.css").read_text(encoding="utf-8")
    assert ".steps-track b" in css and ".step.footsteps" in css
