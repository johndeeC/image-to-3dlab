"""The Music sheet's logic (viewer/studio/music.js), run under Node against the shipped file,
with the real payload the server sends (viewer/music_api.py), and the page wiring read from
the files that ship."""

from __future__ import annotations

import json
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

import music_api

JOB = "a" * 32


def defaults(monkeypatch, installed=True):
    monkeypatch.setattr(music_api, "missing", lambda engine=music_api.ACE_STEP, hub=None: [] if installed else ["its model files"])
    return music_api.defaults_payload()


def _run(body: str, payload: dict):
    url = (STUDIO / "music.js").as_uri()
    program = f"import * as m from {json.dumps(url)};\nconst D = {json.dumps(payload)};\n{body}"
    result = subprocess.run([NODE, "--input-type=module", "--eval", program],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


@needs_node
def test_the_form_starts_instrumental_planned_and_a_minute_long(monkeypatch):
    out = _run("""console.log(JSON.stringify({ form: m.startForm(D),
        lengths: [10, 30, 60, 90, 240, null].map(m.musicLength) }))""", defaults(monkeypatch))
    assert out["form"] == {"prompt": "", "seconds": 60, "singing": False, "lyrics": "", "plan": True, "loop": False}
    assert out["lengths"] == ["10 s", "30 s", "1 min", "1 min 30 s", "4 min", ""]


@needs_node
def test_the_request_is_what_the_form_holds_and_the_server_accepts_it(monkeypatch):
    out = _run("""const form = { ...m.startForm(D), prompt: '  calm village theme  ', seconds: '30' };
      console.log(JSON.stringify({
        plain: m.musicRequest(form, D),
        quick: m.musicRequest({ ...form, plan: false }, D),
        sung: m.musicRequest({ ...form, singing: true, lyrics: ' Heave ho\\naway we go ' }, D),
        hidden: m.musicRequest({ ...form, singing: false, lyrics: 'left in the box' }, D),
        loop: m.musicRequest(m.flip(form, 'loop'), D),
      }))""", defaults(monkeypatch))
    assert out["plain"] == {"prompt": "calm village theme", "seconds": 30, "singing": False, "plan": True,
                            "loop": False, "count": 2}
    assert out["quick"]["plan"] is False and out["loop"]["loop"] is True
    assert out["sung"]["singing"] is True and out["sung"]["lyrics"] == "Heave ho\naway we go"
    assert "lyrics" not in out["hidden"]                 # words left in a hidden box are not sung
    for asked in out.values():                           # what the page sends is what the real server check takes
        music_api.clean_request(dict(asked))


@needs_node
def test_a_loop_and_singing_cannot_both_be_on(monkeypatch):
    out = _run("""const sung = m.flip(m.startForm(D), 'singing'), looped = m.flip(sung, 'loop');
      console.log(JSON.stringify([sung, looped, m.flip(looped, 'singing'), m.flip(looped, 'plan'), m.flip(looped, 'loop')]))""",
               defaults(monkeypatch))
    assert (out[0]["singing"], out[0]["loop"]) == (True, False)
    assert (out[1]["singing"], out[1]["loop"]) == (False, True)       # turning loops on turned singing off
    assert (out[2]["singing"], out[2]["loop"]) == (True, False)       # and the other way round
    assert out[3]["plan"] is False and out[3]["loop"] is True and out[4]["loop"] is False


@needs_node
def test_why_make_cannot_start_is_said_in_plain_words(monkeypatch):
    out = _run("""const ask = (form) => m.musicProblem(m.musicRequest({ ...m.startForm(D), ...form }, D), D);
      console.log(JSON.stringify([ask({ prompt: 'a tune' }), ask({}), ask({ prompt: 'x'.repeat(501) }),
        ask({ prompt: 'a tune', singing: true }), ask({ prompt: 'a tune', singing: true, lyrics: 'la '.repeat(1001) })]))""",
               defaults(monkeypatch))
    assert out[0] is None and out[1] == "Describe the music first." and "500" in out[2]
    assert out[3] == "Write the words to sing, or turn singing off." and "3000" in out[4]
    off = _run("console.log(JSON.stringify([m.musicProblem({ prompt: 'a tune' }, D), m.musicNotInstalledText(D)]))",
               defaults(monkeypatch, installed=False))
    assert off[0] == off[1] == "The music engine is not installed yet. It is a 10.1 GB download. Open Setup to add it."


@needs_node
def test_the_make_button_and_what_a_try_says(monkeypatch):
    out = _run("""console.log(JSON.stringify({ short: m.makeWords(D, { seconds: 30 }), long: m.makeWords(D, { seconds: 180, loop: true }),
        tries: [m.tryWords({ seconds: 29.5, planned: { tempo_bpm: 73, key: 'D major', description: 'A gentle piece.' } }),
                m.tryWords({ seconds: 125 }), m.tryWords(undefined), m.plannedText({ key: 'A minor' })] }))""", defaults(monkeypatch))
    assert out["short"] == {"button": "Make 2", "hint": "2 tries, under a minute. Keep the one you like."}
    assert "a minute or two" in out["long"]["hint"] and "plays round and round" in out["long"]["hint"]
    # the tempo is said in words, not as an abbreviation
    assert out["tries"] == ["30 s · 73 beats a minute · D major", "2 min 5 s", "", "A minor"]


@needs_node
def test_the_progress_line_says_where_it_is_and_how_it_ended(monkeypatch):
    out = _run("""const t = (n) => Array.from({ length: n }, (_, i) => ({ number: i + 1 }));
      console.log(JSON.stringify([m.musicProgress(null), m.musicProgress({ status: 'queued', count: 2 }),
        m.musicProgress({ status: 'running', count: 2, tries: t(1), last_event: { phase: 'making', percent: 61.4 } }),
        m.musicProgress({ status: 'done', count: 2, tries: t(2) }), m.musicProgress({ status: 'done', count: 2, tries: t(1) }),
        m.musicProgress({ status: 'error', error: 'It fell over.' }), m.musicProgress({ status: 'cancelled', count: 2, tries: t(1) })]))""",
               defaults(monkeypatch))
    assert out[0] == "" and out[1] == "Loading the music engine…" and out[2] == "Making try 2 of 2 · 61%"
    assert out[3] == "Done. Play each one and keep what you like." and "1 of 2 came out" in out[4]
    assert out[5] == "It fell over." and out[6] == "Stopped. 1 of 2 made."


@needs_node
def test_make_more_like_this_asks_for_the_same_again_without_the_seed(monkeypatch):
    job = {"job_id": JOB, "status": "done", "kind": "music", "prompt": "battle music", "seconds": 20.0, "seed": 9, "count": 2,
           "music": {"engine": "acestep", "singing": False, "lyrics": None, "loop": True, "plan": False, "seconds": 20.0}}
    out = _run(f"""const again = m.moreMusicLikeThis({json.dumps(job)}, D);
      console.log(JSON.stringify({{ again, form: m.formOf(again) }}))""", defaults(monkeypatch))
    assert out["again"] == {"prompt": "battle music", "seconds": 20, "singing": False, "plan": False, "loop": True, "count": 2}
    assert out["form"] == {"prompt": "battle music", "seconds": 20, "singing": False, "lyrics": "", "plan": False, "loop": True}
    music_api.clean_request(dict(out["again"]))


@needs_node
def test_the_music_sheet_loads_as_a_module():
    """A slip in the file would only show when someone opens the sheet: import the real one."""
    url = (STUDIO / "music-pane.js").as_uri()
    result = subprocess.run([NODE, "--input-type=module", "--eval",
                             f"const m = await import({json.dumps(url)}); console.log(Object.keys(m).join())"],
                            check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "openMusicPane"


@needs_node
def test_music_and_loops_are_not_offered_variations():
    url = (STUDIO / "sound.js").as_uri()
    program = f"""import * as m from {json.dumps(url)};
      console.log(JSON.stringify([m.canVary({{ sound_kind: 'effect' }}), m.canVary({{ sound_kind: 'music' }}),
        m.canVary({{ sound_kind: 'loop' }}), m.canVary({{ kind: 'sound_set' }}), m.keptMeta({{ sound_kind: 'music', seconds: 30 }})]))"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", program], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [True, False, False, False, "Music · 30 s"]


def test_music_opens_from_the_kind_buttons_and_goes_back():
    app = (STUDIO / "app.js").read_text(encoding="utf-8")
    assert "from './music-pane.js'" in app and "music: openMusic" in app and "canVary(sound)" in app
    assert "openMusicPane({ stage: $('stage'), on: { kept: refreshSounds, back:" in app
    assert app.count("music: openMusic") == 2                      # from the Sound sheet and from the Voice sheet
    pane = (STUDIO / "sound-pane.js").read_text(encoding="utf-8")
    assert "on.music?.()" in pane and "sheet === 'music'" in pane
    assert "on.music?.()" in (STUDIO / "voice-pane.js").read_text(encoding="utf-8")
    music = (STUDIO / "music-pane.js").read_text(encoding="utf-8")
    assert "on.back?.()" in music and "on.voice?.()" in music


def test_the_sheet_never_starts_a_download_takes_no_file_and_uses_plain_words():
    pane = (STUDIO / "music-pane.js").read_text(encoding="utf-8")
    logic = (STUDIO / "music.js").read_text(encoding="utf-8")
    assert "/api/setup" not in pane and "download" not in pane.lower()
    assert 'type="file"' not in pane and "FormData" not in pane and "upload" not in pane.lower()
    posts = [line for line in pane.splitlines() if "post(" in line and "import" not in line]
    assert all("/api/music" in line or "/api/sound/" in line for line in posts) and posts
    for text in (pane, logic):
        for jargon in ("DiT", "CFG", "BPM", "VAE", "LLM", "diffusion", "latent", "inference"):
            assert jargon not in text, jargon
    for words in ("Describe the music", "Words to sing", "Length", "Hear the join", "Make more like this"):
        assert words in pane, words


def test_the_words_on_the_switches_come_from_the_server_and_are_plain(monkeypatch):
    told = defaults(monkeypatch)
    assert told["singing"]["label"] == "With singing" and "instrumental" in told["singing"]["hint"]
    assert told["plan"]["label"] == "Plan the song first" and "tempo" in told["plan"]["hint"]
    assert told["loop"]["label"] == "Loops" and "round and round with no click" in told["loop"]["hint"]
