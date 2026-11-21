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
              {"id": "loop", "label": "Background loop", "ready": True,
               "hint": "Plays round and round with no click. Pick it for wind, rain, engines.",
               "seconds": {"min": 4, "max": 30, "default": 8}},
              {"id": "music", "label": "Music", "hint": "", "ready": True, "sheet": "music"},
              {"id": "voice", "label": "Voice", "hint": "", "ready": True, "sheet": "voice"}],
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
def test_every_kind_can_be_picked_and_one_not_built_yet_says_later():
    out = _run("""console.log(JSON.stringify([m.kindButtons(D),
        m.kindButtons({ kinds: [{ id: 'radio', label: 'Radio play', ready: false }] })]))""")
    out, later = out
    assert [(k["label"], k["disabled"], k["pressed"]) for k in out] == [
        ("Effect", False, True), ("Background loop", False, False),
        ("Music", False, False), ("Voice", False, False)]
    assert [(k["label"], k["disabled"]) for k in later] == [("Radio play · later", True)]
    assert out[1]["hint"] == "Plays round and round with no click. Pick it for wind, rain, engines."
    assert [k["sheet"] for k in out] == [None, None, "music", "voice"]    # Music and Voice open sheets of their own


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


VARIATIONS = {
    "min": 2, "max": 12, "default": 6, "default_how_different": "a little",
    "how_different": [{"id": "a little", "label": "A little", "hint": "The same thing, done again."},
                      {"id": "some", "label": "Some", "hint": "Clearly different takes."},
                      {"id": "a lot", "label": "A lot", "hint": "Nearly a different thing."}],
    "change": [{"id": "pitch", "label": "Pitch", "hint": "Higher or lower."}, {"id": "volume", "label": "Volume", "hint": ""}],
}


@needs_node
def test_make_more_like_this_asks_for_the_same_thing_again_with_a_new_seed():
    out = _run("""console.log(JSON.stringify([
        m.moreLikeThis({ prompt: 'a door', seconds: 2.5, leave_out: ['music'], seed: 7, count: 4 }),
        m.moreLikeThis({ prompt: 'a door', seconds: 2.5 }, 2)]))""")
    assert out[0] == {"kind": "effect", "prompt": "a door", "seconds": 2.5, "leave_out": ["music"], "count": 4}
    assert "seed" not in out[0] and out[1]["count"] == 2 and out[1]["leave_out"] == []


@needs_node
def test_the_variations_request_is_what_the_form_holds():
    out = _run("""console.log(JSON.stringify([
        m.variationsRequest({ count: '8', howDifferent: 'some', change: new Set(['pitch']) }),
        m.variationsRequest(),
        m.variationsProblem(m.variationsRequest({ change: [] })),
        m.variationsProblem(m.variationsRequest({ change: ['volume'] })),
        m.sameSet({ count: 4, how_different: 'a lot', change: ['pitch', 'volume'], seed: 0, variations: [] })]))""")
    assert out[0] == {"count": 8, "how_different": "some", "change": ["pitch"]}
    assert out[1] == {"count": 6, "how_different": "a little", "change": []}
    assert out[2] == "Tick at least one thing to change." and out[3] is None
    assert out[4] == {"count": 4, "how_different": "a lot", "change": ["pitch", "volume"], "seed": 0}   # seed 0 is a seed


@needs_node
def test_the_how_different_slider_has_one_stop_per_choice():
    out = _run(f"""const V = {json.dumps(VARIATIONS)};
      console.log(JSON.stringify([m.howDifferentStops(V), m.howDifferentStops(V, 'a lot'), m.howDifferentStops(V, 'nope'),
        m.howDifferentStops(null)]))""")
    assert out[0] == {"max": 2, "at": 0, "id": "a little", "label": "A little", "hint": "The same thing, done again."}
    assert (out[1]["at"], out[1]["label"]) == (2, "A lot") and out[2]["at"] == 0
    assert out[3] == {"max": 0, "at": 0, "label": "", "hint": ""}


@needs_node
def test_takes_are_rows_with_a_plain_note_and_the_playing_one_marked():
    out = _run("""const preview = { variations: [
        { index: 1, file: 'boots_01.wav', url: '/a/1.wav', note: 'the original', seconds: 0.5, bars: [1] },
        { index: 2, file: 'boots_02.wav', url: '/a/2.wav', note: 'a bit higher, softer', seconds: 0.47 }] };
      console.log(JSON.stringify([m.variationRows(preview, '/a/2.wav'), m.variationRows(null)]))""")
    assert out[0] == [
        {"index": 1, "file": "boots_01.wav", "url": "/a/1.wav", "bars": [1], "stem": "boots", "tail": "_01.wav",
         "note": "the original · 0.5 s", "playing": False},
        {"index": 2, "file": "boots_02.wav", "url": "/a/2.wav", "bars": [], "stem": "boots", "tail": "_02.wav",
         "note": "a bit higher, softer · 0.5 s", "playing": True}]
    assert out[1] == []


@needs_node
def test_play_like_a_game_walks_the_servers_order_and_goes_round():
    out = _run("""let played = 0; const seen = [];
      for (let i = 0; i < 7; i += 1) { const next = m.nextInOrder([2, 0, 1], played); played = next.played; seen.push(next.index); }
      console.log(JSON.stringify([seen, played, m.nextInOrder([], 4), m.nextInOrder(undefined)]))""")
    assert out[0] == [2, 0, 1, 2, 0, 1, 2] and out[1] == 7
    assert out[2] == {"index": 0, "played": 5} and out[3] == {"index": 0, "played": 1}


def test_the_order_the_page_walks_is_the_real_one_and_never_repeats_going_round():
    """The page has no shuffle of its own: it walks sound_post.play_order, which is safe to loop."""
    from itertools import pairwise

    from image_to_3dlab import sound_post
    for seed in range(50):
        order = sound_post.play_order(5, 8, seed)
        walked = [order[i % len(order)] for i in range(len(order) * 3)]
        assert all(a != b for a, b in pairwise(walked))
    logic = (STUDIO / "sound.js").read_text(encoding="utf-8") + (STUDIO / "variations-pane.js").read_text(encoding="utf-8")
    assert "Math.random" not in logic


@needs_node
def test_sets_ids_and_the_save_hint():
    out = _run("""console.log(JSON.stringify({
        meta: [m.keptMeta({ kind: 'sound_set', count: 6, sound_kind: 'effect' }), m.keptMeta({ kind: 'sound', sound_kind: 'effect', seconds: 1 })],
        set: [m.isSet({ kind: 'sound_set' }), m.isSet({ kind: 'sound' }), m.isSet(null)],
        ids: [m.soundIdOf('sounds/boots__20261120-091306/sound.wav'), m.soundIdOf('sounds/../x/sound.wav'),
              m.soundIdOf('sounds/a_set__20261120-091306/a_01.wav'), m.soundIdOf(null)],
        hint: m.saveSetHint(6),
        found: m.filterSounds([{ kind: 'sound_set', name: 'Boots', prompt: 'gravel' }], 'gravel').length }))""")
    assert out["meta"] == ["Set · 6 sounds", "Effect · 1 s"] and out["set"] == [True, False, False]
    assert out["ids"] == ["boots__20261120-091306", None, None, None]
    assert out["hint"] == ("6 numbered files in one folder, ready to drop into Unity, Godot or Roblox. "
                           "One licence record covers them all.")
    assert out["found"] == 1


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


# ------------------------------------------------------------------ background loops

@needs_node
def test_a_loop_has_its_own_lengths_and_its_own_words():
    out = _run("""console.log(JSON.stringify({
        loop: m.kindLimits(D, 'loop'), effect: m.kindLimits(D, 'effect'), other: m.kindLimits(D, 'nope'),
        words: m.formWords('loop'), plain: m.formWords('effect'),
        request: m.soundRequest({ prompt: 'rain on a tent', seconds: 10, kind: 'loop' }),
        again: m.moreLikeThis({ kind: 'loop', prompt: 'rain on a tent', seconds: 10, leave_out: [] }) }))""")
    assert out["loop"] == {"min": 4, "max": 30, "usual": 8}
    assert out["effect"] == out["other"] == {"min": 0.5, "max": 30, "usual": 1.5}
    assert out["words"]["placeholder"] == "Rain on a tent" and "goes on and on" in out["words"]["describe"]
    assert "comes round again" in out["words"]["length"] and "ringing" in out["plain"]["length"]
    assert set(out["words"]) == set(out["plain"])
    assert out["request"] == {"kind": "loop", "prompt": "rain on a tent", "seconds": 10, "leave_out": [], "count": 4}
    assert out["again"] == out["request"]                    # more like this stays a loop


@needs_node
def test_what_counts_as_a_loop_and_where_its_join_is_heard():
    out = _run("""console.log(JSON.stringify({
        is: [m.isLoop({ kind: 'loop' }), m.isLoop({ kind: 'sound', sound_kind: 'loop' }), m.isLoop({ kind: 'sound', loop: true }),
             m.isLoop({ kind: 'effect' }), m.isLoop({ kind: 'sound', sound_kind: 'effect', loop: false }), m.isLoop(null)],
        join: [m.joinWindow(8), m.joinWindow(30), m.joinWindow(3), m.joinWindow(0)] }))""")
    assert out["is"] == [True, True, True, False, False, False]
    # the last two seconds, round the join, into the first two; a short loop gives half of itself each side
    assert out["join"] == [{"offset": 6, "duration": 4}, {"offset": 28, "duration": 4},
                           {"offset": 1.5, "duration": 3}, {"offset": 0, "duration": 0}]


def test_a_loop_plays_round_in_the_sheet_and_the_library_and_its_join_can_be_heard():
    pane = (STUDIO / "sound-pane.js").read_text(encoding="utf-8")
    assert "node.loop = true" in pane and "createBufferSource" in pane       # joined sample to sample, not a gap
    assert "joinWindow(node.buffer.duration)" in pane and "node.start(0, part.offset, part.duration)" in pane
    assert "Hear the join" in pane and "player.toggle(t.dataset.join, { join: true })" in pane
    assert "{ loop: t.dataset.loop !== undefined }" in pane and "isLoop(job)" in pane
    assert "form.seconds = kindLimits(defaults, form.kind).usual" in pane    # picking the kind sets its usual length
    tip = re.search(r'data-join=[^>]*data-tip="([^"]+)"', pane)[1]
    assert "no click" in tip and len(tip) < 140
    app = (STUDIO / "app.js").read_text(encoding="utf-8")
    assert "soundPlayer.toggle(url(sound.file), { loop: isLoop(sound) })" in app
    assert "playing round and round" in app


def test_loop_words_on_screen_are_plain():
    """No sound-engineer words where a user reads: the control is "Hear the join"."""
    shown = "".join((STUDIO / name).read_text(encoding="utf-8") for name in ("sound-pane.js", "sound.js"))
    said = " ".join(re.findall(r">([A-Z][^<>{}$=;()]*)<", shown) + re.findall(r"'([A-Z][^'\n]{8,})'", shown)
                    + re.findall(r'data-tip="([^"]+)"', shown)).lower()
    for jargon in ("lufs", "crossfade", "smpl", "chunk", "rms", "seam", "decibel", " db", "sample", "equal power"):
        assert jargon not in said, jargon
    assert "hear the join" in said and "round and round" in said


@needs_node
def test_the_sound_sheet_loads_as_a_module():
    """A slip in the file would only show when someone opens the sheet: import the real one."""
    url = (STUDIO / "sound-pane.js").as_uri()
    result = subprocess.run([NODE, "--input-type=module", "--eval",
                             f"const m = await import({json.dumps(url)}); console.log(Object.keys(m).sort().join())"],
                            check=True, capture_output=True, text=True)
    assert "createPlayer" in result.stdout and "openSoundPane" in result.stdout


# ------------------------------------------------------------------ variations, as they ship

@needs_node
def test_the_variations_sheet_loads_as_a_module():
    """A slip in the file would only show when someone opens the sheet: import the real one."""
    url = (STUDIO / "variations-pane.js").as_uri()
    result = subprocess.run([NODE, "--input-type=module", "--eval",
                             f"const m = await import({json.dumps(url)}); console.log(Object.keys(m).join())"],
                            check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "openVariationsPane"


def test_variations_open_from_a_kept_sound_in_the_library_and_from_a_kept_try():
    app = (STUDIO / "app.js").read_text(encoding="utf-8")
    assert "from './variations-pane.js'" in app and "openVariationsPane({ stage: $('stage'), sound" in app
    assert 'data-variations="${escape(sound.id)}"' in app and "variations: openVariations" in app
    assert "!canVary(sound) ? ''" in app                  # a set is not varied again, nor is a loop
    pane = (STUDIO / "sound-pane.js").read_text(encoding="utf-8")
    assert 'data-variations="${esc(soundIdOf(card.kept))}"' in pane and "on.variations?.(" in pane
    assert ">Make variations</button>" in pane and ">Make variations</button>" in app


def test_a_try_can_ask_for_more_like_it():
    pane = (STUDIO / "sound-pane.js").read_text(encoding="utf-8")
    assert ">Make more like this</button>" in pane and "moreLikeThis(job, defaults.limits.tries)" in pane
    tip = re.search(r'data-more [^>]*data-tip="([^"]+)"', pane)[1]
    assert "same description" in tip and "cleared" in tip and len(tip) < 140


def test_the_variations_sheet_says_what_the_design_says_in_plain_words():
    pane = (STUDIO / "variations-pane.js").read_text(encoding="utf-8")
    logic = (STUDIO / "sound.js").read_text(encoding="utf-8")
    for said in ("Make <span>variations", "How many", "How different", "What changes", "Play like a game",
                 "Save the set", "Make ${form.count} variations", "Save ${preview.count} sounds",
                 "Small changes so repeats don't sound copied.",
                 "never the same one twice running, the way your game engine will"):
        assert said in pane, said
    assert "ready to drop into Unity, Godot or Roblox. One licence record covers them all." in logic
    words = " ".join(re.findall(r">([A-Z][^<>{}$=;()]*)<", pane)).lower() + " ".join(
        re.findall(r"'([A-Z][^'\n]{8,})'", pane)).lower()
    assert "how many" in words and "save the set" in words               # the reader does see the text
    for jargon in ("semitone", "decibel", " db", "resampl", "seed", "provenance", "sidecar", "model run", "khz"):
        assert jargon not in words, jargon


def test_the_variations_sheet_only_talks_to_the_variation_endpoints():
    pane = (STUDIO / "variations-pane.js").read_text(encoding="utf-8")
    assert set(re.findall(r"post\(\s*[`']([^`'$]+)", pane)) == {"/api/sounds/"}
    assert sorted(re.findall(r"/api/sounds/\$\{encodeURIComponent\(sound\.id\)\}/(\w+)", pane)) == ["set", "variations"]
    assert "sameSet(preview)" in pane                                    # Save sends back what was heard
    assert "/api/setup" not in pane and "/api/sound'" not in pane        # no model, no download


def test_the_variations_sheet_has_its_styles():
    css = (STUDIO / "studio.css").read_text(encoding="utf-8")
    for rule in (".variations-sheet", ".var-rows", ".var-row", ".var-save", ".game", ".sound-row"):
        assert rule in css, rule
    assert "#fd6d14" not in css.split("/* Make variations")[1].split("/*")[0]
