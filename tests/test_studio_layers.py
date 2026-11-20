"""The Layers screen's logic (viewer/studio/layers.js), run under Node against the shipped
file, and the page wiring read from the files that ship."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUDIO = REPO / "viewer" / "studio"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is required to execute browser ES modules")

sys.path[:0] = [str(REPO), str(REPO / "viewer")]


def options():
    import sound_layers_api
    return sound_layers_api.options_payload()          # the real limits and licence choices


def _run(body: str):
    url = (STUDIO / "layers.js").as_uri()
    program = f"import * as m from {json.dumps(url)};\nconst O = {json.dumps(options())};\n{body}"
    result = subprocess.run([NODE, "--input-type=module", "--eval", program],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


STAGED = {"layer": "a" * 32, "label": "arrow whoosh", "from": "generated", "seconds": 1.0, "bars": [1, 0.5],
          "licence": "Apache-2.0", "checked": True, "where": None, "url": "/api/sound/layers/" + "a" * 32 + ".wav"}


@needs_node
def test_the_split_becomes_rows_to_make_and_says_what_it_did():
    out = _run("""const split = { rule: 'thrown-hit', layers: [
        { role: 'lead-in', label: 'Lead-in', describe: 'arrow whoosh past', start_ms: 0 },
        { role: 'hit', label: 'Hit', describe: 'arrow thud', start_ms: 250 }] };
      const rows = m.layersFromSplit(split);
      console.log(JSON.stringify({ rows: rows.map((r) => [r.part, r.describe, r.start_ms, r.layer, r.count]),
        keys: new Set(rows.map((r) => r.key)).size, line: m.splitLine(split),
        plain: m.splitLine({ rule: null, layers: [{ describe: 'coin' }] }), none: m.splitLine(null),
        source: m.layerSourceLine(rows[0]), left: m.notMadeCount(rows) }))""")
    assert out["rows"] == [["Lead-in", "arrow whoosh past", 0, None, 1], ["Hit", "arrow thud", 250, None, 1]]
    assert out["keys"] == 2 and out["left"] == 2 and out["source"] == "Not made yet"
    assert out["line"] == "Split into 2 layers. Change any description, then make each one."
    assert out["plain"].startswith("No split for this one, so it is one layer as you typed it.") and out["none"] == ""


@needs_node
def test_a_made_layer_keeps_where_it_sits_and_says_where_it_is_from():
    out = _run(f"""const S = {json.dumps(STAGED)};
      const row = m.madeLayer(m.newLayer({{ part: 'Hit', describe: 'arrow whoosh past', start_ms: 250 }}), S);
      console.log(JSON.stringify({{ row: [row.layer, row.label, row.start_ms, row.part, row.job],
        lines: [m.layerSourceLine(row), m.layerSourceLine({{ ...row, from: 'file', licence: 'CC0' }}),
                m.layerSourceLine({{ ...row, from: 'kept', licence: null }})] }}))""")
    assert out["row"] == ["a" * 32, "arrow whoosh", 250, "Hit", None]
    assert out["lines"] == ["Described · Apache-2.0", "Your file · CC0", "From your Library"]


@needs_node
def test_the_request_holds_only_made_layers_in_order_with_their_numbers():
    out = _run(f"""const S = {json.dumps(STAGED)};
      const rows = [m.newLayer({{ ...S, volume_db: -6 }}), m.newLayer({{ describe: 'not made' }}),
                    m.newLayer({{ ...S, layer: '{"b" * 32}', start_ms: '250', pitch_semitones: 2 }})];
      console.log(JSON.stringify([m.stackRequest(rows, '  Arrow hit '), m.stackRequest(rows), m.stackRequest([])]))""")
    assert out[0] == {"name": "Arrow hit", "layers": [
        {"layer": "a" * 32, "start_ms": 0, "volume_db": -6, "pitch_semitones": 0},
        {"layer": "b" * 32, "start_ms": 250, "volume_db": 0, "pitch_semitones": 2}]}
    assert "name" not in out[1] and out[2] == {"layers": []}


@needs_node
def test_a_stack_that_cannot_be_played_says_why():
    out = _run(f"""const S = {json.dumps(STAGED)};
      const made = (extra) => m.newLayer({{ ...S, ...extra }});
      console.log(JSON.stringify([
        m.stackProblem([], O), m.stackProblem([m.newLayer({{ describe: 'x' }})], O), m.stackProblem([made()], O),
        m.stackProblem([{{ ...made(), start_ms: -5 }}], O), m.stackProblem([{{ ...made(), volume_db: 13 }}], O),
        m.stackProblem([{{ ...made(), pitch_semitones: NaN }}], O),
        m.stackProblem(Array.from({{ length: 9 }}, () => made()), O)]))""")
    assert out == ["Add a layer first.", "Make a layer first: press Make on one.", None,
                   "Starts at must be between 0 and 10000.", "Volume must be between -40 and 12.",
                   "Pitch must be a number.", "A sound can have up to 8 layers."]


def test_the_limits_the_page_checks_are_the_ones_the_mix_refuses():
    from image_to_3dlab import sound_post
    told = options()
    assert told["limits"] == {key: {"min": low, "max": high} for key, (low, high) in sound_post.LAYER_LIMITS.items()}
    assert told["max"] == sound_post.MAX_LAYERS


@needs_node
def test_the_timeline_places_blocks_by_start_and_length_and_pitch_changes_length():
    out = _run(f"""const S = {json.dumps(STAGED)};
      const rows = [m.newLayer(S), m.newLayer({{ ...S, start_ms: 250, seconds: 0.5, pitch_semitones: 12 }}),
                    m.newLayer({{ describe: 'todo', start_ms: 500 }})];
      const t = m.timeline(rows);
      const long = m.timeline([m.newLayer({{ ...S, start_ms: 2000, seconds: 1.6, pitch_semitones: -12 }})]);
      console.log(JSON.stringify({{ span: t.span, marks: t.marks.map((x) => [x.at, x.label]), blocks: t.blocks.map((b) => [b.left, b.width, b.made]),
        secs: [m.layerSeconds(rows[1]), m.layerSeconds(rows[2])], long: [long.span, long.marks.length, long.blocks[0].left],
        empty: m.timeline([]).span }}))""")
    assert out["span"] == 1 and out["empty"] == 1
    assert out["marks"] == [[0, "0 s"], [25, "0.25"], [50, "0.5"], [75, "0.75"], [100, "1 s"]]
    assert out["blocks"] == [[0, 100, True], [25, 25, True], [50, 0, False]]      # an octave up: half as long
    assert out["secs"] == [0.25, 0]
    assert out["long"][0] == 6 and out["long"][2] == pytest.approx(100 * 2 / 6)    # an octave down: 3.2 s, ends at 5.2


@needs_node
def test_small_words_signs_titles_and_making_a_layer():
    out = _run("""console.log(JSON.stringify({
        signs: [m.signed(0), m.signed(1), m.signed(-2), m.signed('-6'), m.signed(1.25), m.signed(null)],
        titles: [m.layersTitle('Arrow hit', [1, 2]), m.layersTitle('', [1]), m.layersTitle(null, [])],
        make: m.layerMakeRequest({ describe: '  arrow thud ' }, '2', 1.5),
        edit: [m.canEditLayers({ kind: 'sound', layers: 2 }), m.canEditLayers({ kind: 'sound', layers: 0 }),
               m.canEditLayers({ kind: 'sound_set', layers: 2 }), m.canEditLayers(null)] }))""")
    assert out["signs"] == ["0", "+1", "−2", "−6", "+1.3", "0"]
    assert out["titles"] == ["Arrow hit · 2 layers", "New sound · 1 layer", "New sound · 0 layers"]
    assert out["make"] == {"kind": "effect", "prompt": "arrow thud", "seconds": 1.5, "leave_out": [], "count": 2}
    assert out["edit"] == [True, False, False, False]


@needs_node
def test_the_finished_licence_is_the_servers_once_heard_and_never_claims_more_before():
    out = _run(f"""const S = {json.dumps(STAGED)};
      const a = m.newLayer(S), cc0 = m.newLayer({{ ...S, licence: 'CC0' }}), bad = m.newLayer({{ ...S, licence: 'Not checked', checked: false }});
      console.log(JSON.stringify([m.finishedLicence([]), m.finishedLicence([a, a]), m.finishedLicence([a, cc0]),
        m.finishedLicence([a, bad]), m.finishedLicence([a, cc0], {{ licence: 'Apache-2.0 + CC0', checked: true }}),
        m.finishedLicence([a, cc0], {{ licence: 'Apache-2.0 + CC-BY', checked: false }})]))""")
    assert out[0]["name"] == "" and out[0]["checked"] is None
    assert (out[1]["name"], out[1]["checked"]) == ("Apache-2.0", True)
    # two different licences: only the server knows if they are the same kind, so say nothing yet
    assert (out[2]["name"], out[2]["checked"]) == ("Apache-2.0 + CC0", False) and "Play together" in out[2]["note"]
    assert (out[3]["name"], out[3]["checked"]) == ("Apache-2.0 + Not checked", False) and out[3]["note"].startswith("Not checked")
    assert (out[4]["name"], out[4]["checked"]) == ("Apache-2.0 + CC0", True)
    assert out[4]["note"] == "Every layer is listed in its record. If one layer says Not checked, so does the finished sound."
    assert out[5]["checked"] is False and "different kinds of licence" in out[5]["note"]


@needs_node
def test_your_own_file_starts_at_not_checked_and_bad_files_are_said():
    out = _run("""console.log(JSON.stringify({
        first: m.licenceChoice(O), cc: m.licenceChoice(O, 'cc-by'), odd: m.licenceChoice(O, 'gpl').id,
        files: [m.fileProblem(null, O), m.fileProblem({ name: 'song.mp3', size: 10 }, O),
                m.fileProblem({ name: 'big.WAV', size: 21 * 1024 * 1024 }, O), m.fileProblem({ name: 'ok.wav', size: 10 }, O)] }))""")
    assert (out["first"]["id"], out["first"]["label"], out["first"]["needsLink"]) == ("not-checked", "Not checked", False)
    assert out["cc"]["label"] == "CC-BY (credit needed)" and out["cc"]["needsLink"] is True and out["odd"] == "not-checked"
    assert out["files"] == ["Pick a file first.", "Bring a WAV file (its name ends in .wav).",
                            "That file is larger than 20 MB.", None]


# ------------------------------------------------------------------ the page, as it ships

@needs_node
def test_the_layers_screen_loads_as_a_module():
    url = (STUDIO / "layers-pane.js").as_uri()
    result = subprocess.run([NODE, "--input-type=module", "--eval",
                             f"const m = await import({json.dumps(url)}); console.log(Object.keys(m).join())"],
                            check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "openLayersPane"


def test_layers_open_from_the_sound_sheet_and_from_a_kept_sound_built_from_layers():
    app = (STUDIO / "app.js").read_text(encoding="utf-8")
    assert "from './layers-pane.js'" in app and "openLayersPane({ stage: $('stage'), description, sound" in app
    assert "layers: openLayers" in app and 'data-layers="${escape(sound.id)}"' in app
    assert "canEditLayers(sound) ?" in app and ">Edit layers</button>" in app       # only a sound with a stack
    pane = (STUDIO / "sound-pane.js").read_text(encoding="utf-8")
    assert ">Build it from layers</button>" in pane and "on.layers?.({ description: form.prompt })" in pane
    tip = re.search(r'data-layers data-tip="([^"]+)"', pane)[1]
    assert "on its own" in tip and len(tip) < 140


def test_the_screen_says_what_the_design_says_in_plain_words():
    pane = (STUDIO / "layers-pane.js").read_text(encoding="utf-8")
    logic = (STUDIO / "layers.js").read_text(encoding="utf-8")
    for said in ("+ Add a layer", ">Describe it<", ">Use my own file<", "Starts at", "Volume", "Pitch", "Play together",
                 "Save as one sound", "Where it's from", ">Licence<", "All together", "Licence of the finished sound"):
        assert said in pane, said
    assert "If one layer says Not checked, so does the finished sound." in logic
    words = " ".join(re.findall(r">([A-Z][^<>{}$=;()]*)<", pane)).lower() + " ".join(
        re.findall(r"'([A-Z][^'\n]{8,})'", pane + logic)).lower()
    assert "starts at" in words and "play together" in words               # the reader does see the text
    for jargon in ("semitone", "decibel", " db", "resampl", "seed", "provenance", "sidecar", "khz", "mono", "normalis",
                   "limiter", "unverified", "classification"):
        assert jargon not in words, jargon


def test_the_screen_does_not_promise_dragging():
    """Blocks are placed with Starts at. Dragging is not built, so nothing on screen says it is."""
    shown = (STUDIO / "layers-pane.js").read_text(encoding="utf-8") + (STUDIO / "layers.js").read_text(encoding="utf-8")
    assert "drag" not in shown.lower() and "pointerdown" not in shown and "mousedown" not in shown


def test_one_code_path_the_server_mixes_and_the_page_only_plays():
    shown = (STUDIO / "layers-pane.js").read_text(encoding="utf-8") + (STUDIO / "layers.js").read_text(encoding="utf-8")
    assert "AudioContext" not in shown and "OfflineAudioContext" not in shown
    pane = (STUDIO / "layers-pane.js").read_text(encoding="utf-8")
    assert "post(`/api/sound/layers/${action}`, stackRequest(rows, name))" in pane       # mix and save send the same stack
    posted = set(re.findall(r"(?:post|sendFile)\(\s*[`']([^`'$]+)", pane))
    assert posted == {"/api/sound", "/api/sound/", "/api/sound/layers/", "/api/sound/layers/add",
                      "/api/sound/layers/upload", "/api/sound/layers/split", "/api/sounds/"}
    assert "/api/setup" not in pane and 'href="./setup.html"' in pane                    # never fetches the engine itself


def test_the_screen_has_its_styles_in_the_studios_own_colours():
    css = (STUDIO / "studio.css").read_text(encoding="utf-8")
    for rule in (".layers-card", ".layer-row", ".layer-track", ".layer-block", ".layer-knobs", ".layer-add",
                 ".layer-tries", ".layers-all", ".layers-licence", ".layer-ruler"):
        assert rule in css, rule
    section = css.split("/* Layers (layers-pane.js)")[1]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", section)
