"""The Sound sheet's logic (viewer/studio/sound.js), run under Node against the shipped file,
and the page wiring read from the files that ship."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUDIO = REPO / "viewer" / "studio"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is required to execute browser ES modules")

DEFAULTS = {
    "installed": True, "download_size": "6.4 GB", "setup_id": "sound-effects",
    "kinds": [{"id": "effect", "label": "Effect", "hint": "One short sound, like a footstep or a hit.", "ready": True},
              {"id": "loop", "label": "Background loop", "hint": "", "ready": False},
              {"id": "music", "label": "Music", "hint": "", "ready": False},
              {"id": "voice", "label": "Voice", "hint": "", "ready": False}],
    "leave_out": ["music", "voices", "room echo"],
    "limits": {"min_seconds": 0.5, "max_seconds": 30, "default_seconds": 1.5, "max_prompt": 500, "tries": 4},
}


def _run(body: str):
    url = (STUDIO / "sound.js").as_uri()
    program = f"import * as m from {json.dumps(url)};\nconst D = {json.dumps(DEFAULTS)};\n{body}"
    result = subprocess.run([NODE, "--input-type=module", "--eval", program],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


@needs_node
def test_the_request_is_what_the_form_holds():
    out = _run("""console.log(JSON.stringify(m.soundRequest({ prompt: '  a door  ', seconds: '2.5',
        leaveOut: new Set(['music', 'voices']) })))""")
    assert out == {"kind": "effect", "prompt": "a door", "seconds": 2.5, "leave_out": ["music", "voices"], "count": 4}


@needs_node
def test_only_effect_can_be_picked_and_the_rest_say_later():
    out = _run("console.log(JSON.stringify(m.kindButtons(D)))")
    assert [(k["label"], k["disabled"], k["pressed"]) for k in out] == [
        ("Effect", False, True), ("Background loop · later", True, False),
        ("Music · later", True, False), ("Voice · later", True, False)]


@needs_node
def test_a_missing_engine_is_said_in_plain_words_and_points_to_setup():
    out = _run("""const off = { ...D, installed: false };
      console.log(JSON.stringify([m.requestProblem({ prompt: 'a door' }, off), m.requestProblem({ prompt: '' }, D),
        m.requestProblem({ prompt: 'x'.repeat(501) }, D), m.requestProblem({ prompt: 'a door' }, D),
        m.notInstalledText(null)]))""")
    assert out[0] == "The sound engine is not installed yet. It is a 6.4 GB download. Open Setup to add it."
    assert out[1] == "Describe the sound first." and "500" in out[2] and out[3] is None
    assert out[4] == "The sound engine is not installed yet. Open Setup to add it."


@needs_node
def test_four_cards_from_the_start_filling_in_as_tries_land():
    out = _run("""const t = (n) => ({ number: n, seconds: 1.2, bars: [1, 0.5], url: '/api/sound/j/try-' + n + '.wav', kept: null });
      const states = (job) => m.tryCards(job).map((c) => c.state);
      console.log(JSON.stringify({
        none: states(null),
        loading: states({ status: 'running', count: 4, tries: [] }),
        two: states({ status: 'running', count: 4, tries: [t(1), t(2)] }),
        done: states({ status: 'done', count: 4, tries: [t(1), t(2), t(4)] }),
        queued: states({ status: 'queued', count: 2, tries: [] }),
        card: m.tryCards({ status: 'done', count: 1, tries: [{ ...t(1), kept: 'sounds/a__1/sound.wav' }] })[0] }))""")
    assert out["none"] == ["empty"] * 4
    assert out["loading"] == ["making", "waiting", "waiting", "waiting"]
    assert out["two"] == ["ready", "ready", "making", "waiting"]
    assert out["done"] == ["ready", "ready", "missing", "ready"]       # try 3 came out silent
    assert out["queued"] == ["waiting", "waiting"]
    assert out["card"] == {"number": 1, "state": "ready", "seconds": 1.2, "bars": [1, 0.5],
                           "url": "/api/sound/j/try-1.wav", "kept": "sounds/a__1/sound.wav"}


@needs_node
def test_the_progress_line_says_where_it_is_and_how_it_ended():
    out = _run("""const t = { number: 1 };
      console.log(JSON.stringify([
        m.progressLine(null),
        m.progressLine({ status: 'running', count: 4, tries: [], last_event: { phase: 'loading' } }),
        m.progressLine({ status: 'running', count: 4, tries: [t], last_event: { phase: 'making', percent: 37.5 } }),
        m.progressLine({ status: 'done', count: 4, tries: [t, t, t, t] }),
        m.progressLine({ status: 'done', count: 4, tries: [t, t, t] }),
        m.progressLine({ status: 'error', error: 'Try 1: the sound is silent.' }),
        m.progressLine({ status: 'cancelled', count: 4, tries: [t] })]))""")
    assert out == ["", "Loading the sound engine…", "Making try 2 of 4 · 38%",
                   "Done. Play each one and keep what you like.",
                   "3 of 4 came out. The rest were silent or broken, so they were dropped.",
                   "Try 1: the sound is silent.", "Stopped. 1 of 4 made."]


@needs_node
def test_small_words_lengths_bars_and_kept_lines():
    out = _run("""console.log(JSON.stringify({
        lengths: [m.lengthText(1.5), m.lengthText(30), m.lengthText(0.449), m.lengthText(null)],
        bars: m.barHeights([1, 0.5, 0, 7, 'x']),
        meta: [m.keptMeta({ sound_kind: 'effect', seconds: 1.2 }), m.keptMeta({ seconds: null }), m.keptMeta({ sound_kind: 'loop', seconds: 8 })],
        found: m.filterSounds([{ name: 'Sword on shield', prompt: 'dry hit' }, { name: 'Coin', prompt: null }], 'HIT sword').map((s) => s.name),
        all: m.filterSounds([{ name: 'Coin' }], '  ').length,
        foot: [m.soundsFoot([]), m.soundsFoot([{}])] }))""")
    assert out["lengths"] == ["1.5 s", "30 s", "0.4 s", ""]
    assert out["bars"] == [64, 32, 3, 64, 3]
    assert out["meta"] == ["Effect · 1.2 s", "Sound", "Background loop · 8 s"]
    assert out["found"] == ["Sword on shield"] and out["all"] == 1
    assert out["foot"] == ["0 sounds · saved in output/sounds on this machine", "1 sound · saved in output/sounds on this machine"]


# ------------------------------------------------------------------ the page, as it ships

def test_the_studio_has_a_sound_button_beside_create_and_canvas():
    page = (REPO / "viewer" / "studio.html").read_text(encoding="utf-8")
    row = re.search(r'<div class="make-row">(.*?)</div>', page, re.DOTALL)[1]
    assert re.findall(r'id="(\w+Btn)"', row) == ["createBtn", "canvasBtn", "soundBtn"]
    tip = re.search(r'id="soundBtn" data-tip="([^"]+)"', row)[1]
    assert "sound" in tip.lower() and len(tip) < 140
    assert 'data-filter="sounds"' in page                    # kept sounds have their own Library filter
    app = (STUDIO / "app.js").read_text(encoding="utf-8")
    assert "from './sound-pane.js'" in app and "$('soundBtn').addEventListener('click'" in app


def test_the_sheet_never_starts_a_download_itself():
    """With the engine missing it says so and links to Setup. The only things it may POST to
    are the sound job endpoints: nothing under /api/setup."""
    pane = (STUDIO / "sound-pane.js").read_text(encoding="utf-8")
    assert "/api/setup" not in pane and "download" not in pane.lower().replace("download_size", "")
    assert 'href="./setup.html"' in pane and "notInstalledText" in pane
    posted = set(re.findall(r"post\(\s*[`']([^`'$]+)", pane))
    assert posted == {"/api/sound", "/api/sound/"}


def test_the_words_on_screen_are_plain():
    """Studio text is for the user: no engine names or audio jargon on the sheet itself."""
    shown = (STUDIO / "sound-pane.js").read_text(encoding="utf-8") + (STUDIO / "sound.js").read_text(encoding="utf-8")
    words = " ".join(re.findall(r">([A-Z][^<>{}$=;()]*)<", shown)).lower() + " ".join(
        re.findall(r"'([A-Z][^'\n]{8,})'", shown)).lower()
    for jargon in ("negative prompt", "sample rate", "khz", "inference", "cfg", "mlx", "seed", "wav", "provenance"):
        assert jargon not in words, jargon
    assert "describe it" in words and "in your library" in words          # the reader does see the text
    for said in ("Make a <span>sound", "What kind?", "Describe it", "Leave out", "Length", "In your Library"):
        assert said in shown, said
    assert "Make ${" in shown or "Make 4" in shown


def test_the_sheet_has_its_styles():
    css = (STUDIO / "studio.css").read_text(encoding="utf-8")
    for rule in (".sound-sheet", ".sound-form", ".sound-tries", ".try-card", ".wave", ".kept-strip"):
        assert rule in css, rule
    assert "#fd6d14" not in css.split("/* Sound sheet")[1].split("/*")[0]      # the studio's own colours, by name
