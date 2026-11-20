"""The Voice sheet's logic (viewer/studio/voice.js), run under Node against the shipped file,
with the real payload the server sends (viewer/voice_api.py), and the page wiring read from
the files that ship."""

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

import voice_api

JOB = "a" * 32
AGAIN = "b" * 32


def defaults(monkeypatch, installed=("kokoro", "qwen")):
    monkeypatch.setattr(voice_api, "missing", lambda engine, hub=None: [] if engine.id in installed else ["its model files"])
    return voice_api.defaults_payload()


def _run(body: str, payload: dict):
    url = (STUDIO / "voice.js").as_uri()
    program = f"import * as m from {json.dumps(url)};\nconst D = {json.dumps(payload)};\n{body}"
    result = subprocess.run([NODE, "--input-type=module", "--eval", program],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def tries(job_id, rows):
    return [{"number": n, "line": line, "take": take, "seconds": 1.0 + n / 10, "bars": [1.0],
             "url": f"/api/sound/{job_id}/try-{n}.wav", "kept": None, "guessed": guessed}
            for n, line, take, guessed in rows]


@needs_node
def test_the_engines_are_named_by_what_they_do(monkeypatch):
    out = _run("""console.log(JSON.stringify({ buttons: m.engineButtons(D, 'qwen'), first: m.engineOf(D, 'nope').id,
        off: m.voiceNotInstalledText(m.engineOf(D, 'kokoro')), words: [m.makeWords(m.engineOf(D, 'qwen')), m.makeWords(m.engineOf(D, 'kokoro'))],
        speed: [m.speedText(1), m.speedText(1.2)] }))""", defaults(monkeypatch, installed=("qwen",)))
    assert [(b["label"], b["pressed"], b["installed"]) for b in out["buttons"]] == [
        ("Clear and quick", False, False), ("With feeling", True, True)]
    assert out["first"] == "kokoro"
    assert out["off"] == '"Clear and quick" is not installed yet. It is a 0.34 GB download. Open Setup to add it.'
    assert out["words"][0]["button"] == "Make 4" and "4 tries of each line" in out["words"][0]["hint"]
    assert out["words"][1]["button"] == "Make 1" and "the same way every time" in out["words"][1]["hint"]
    assert out["speed"] == ["1×", "1.2×"]


@needs_node
def test_the_request_holds_only_what_the_engine_takes_and_the_server_accepts_it(monkeypatch):
    payload = defaults(monkeypatch)
    out = _run("""const q = m.engineOf(D, 'qwen'), k = m.engineOf(D, 'kokoro');
      const form = { text: '  Halt! Who goes there?  ', voice: 'ono_anna', direction: 'shout', ownWords: '', speed: 1.2 };
      console.log(JSON.stringify({
        qwen: m.voiceRequest(form, q),
        own: m.voiceRequest({ ...form, ownWords: ' like a pirate ', language: 'english' }, q),
        kokoro: m.voiceRequest(form, k),
        again: m.voiceRequest(form, q, { lines: ['Go away.'], using: ['x', 'y'] }),
        voices: [m.voiceOf(q, 'af_heart').id, m.voiceOf(k, 'bf_emma').id, m.languageOf(q, 'sohee'), m.languageOf(q, 'ryan')],
      }))""", payload)
    assert out["qwen"] == {"engine": "qwen", "voice": "ono_anna", "count": 4, "text": "Halt! Who goes there?",
                           "direction": "shout", "language": "japanese"}         # the voice's own language
    assert out["own"]["direction"] == "like a pirate" and out["own"]["language"] == "english"
    # Kokoro takes no direction and no language; the Qwen voice picked falls back to Kokoro's own
    assert out["kokoro"] == {"engine": "kokoro", "voice": "af_heart", "count": 1, "text": "Halt! Who goes there?",
                             "speed": 1.2}
    assert out["again"]["lines"] == ["Go away."] and out["again"]["using"] == ["x", "y"] and "text" not in out["again"]
    assert out["voices"] == ["ryan", "bf_emma", "korean", "english"]
    # what the page sends is what the real server check takes
    for name in ("qwen", "own", "kokoro", "again"):
        asked = dict(out[name])
        voice_api.clean_request(asked, voice_api.ENGINES[asked.pop("engine")])


@needs_node
def test_why_make_cannot_start_is_said_in_plain_words(monkeypatch):
    out = _run("""const q = m.engineOf(D, 'qwen'), k = m.engineOf(D, 'kokoro');
      console.log(JSON.stringify([m.voiceProblem({ text: 'Hi' }, k, D.limits), m.voiceProblem({ text: '' }, q, D.limits),
        m.voiceProblem({ text: 'x'.repeat(2401) }, q, D.limits), m.voiceProblem({ lines: ['Hi'] }, q, D.limits)]))""",
               defaults(monkeypatch, installed=("qwen",)))
    assert "not installed yet" in out[0] and "Open Setup" in out[0]
    assert out[1] == "Type the line to say first." and "2400" in out[2] and out[3] is None


@needs_node
def test_takes_are_filed_under_their_lines_and_the_first_is_picked(monkeypatch):
    job = {"job_id": JOB, "status": "running", "count": 4,
           "tries": tries(JOB, [(1, 1, 1, []), (2, 1, 2, []), (3, 2, 1, ["Eldoria"])])}
    out = _run(f"""let blocks = m.newBlocks(['Halt!', 'Welcome to Eldoria.']);
      const empty = JSON.parse(JSON.stringify(blocks));
      blocks = m.addTakes(blocks, {json.dumps(job)}, {{ 1: 0, 2: 1 }});
      const twice = m.addTakes(blocks, {json.dumps(job)}, {{ 1: 0, 2: 1 }});
      const picked = m.chooseTake(blocks, 0, '{JOB}:2');
      console.log(JSON.stringify({{ empty, blocks, same: JSON.stringify(twice) === JSON.stringify(blocks),
        cards: m.blockCards(picked[0], '/api/sound/{JOB}/try-1.wav', true), solo: m.blockCards(blocks[1], null, false),
        wrong: m.chooseTake(blocks, 0, 'nope:9')[0].chosen, jobs: m.jobsOnPage(blocks) }}))""", defaults(monkeypatch))
    assert [(b["text"], b["takes"], b["making"]) for b in out["empty"]] == [("Halt!", [], True), ("Welcome to Eldoria.", [], True)]
    first, second = out["blocks"]
    assert [t["number"] for t in first["takes"]] == [1, 2] and first["chosen"] == f"{JOB}:1" and first["making"] is True
    assert [t["number"] for t in second["takes"]] == [3] and second["chosen"] == f"{JOB}:3"
    assert out["same"] is True                                   # polling again does not double the takes
    cards = out["cards"]
    assert [(c["title"], c["chosen"], c["playing"]) for c in cards] == [("Take 1", False, True), ("Take 2", True, False)]
    assert cards[0]["words"] == "1.1 s" and out["solo"][0]["words"] == "1.3 s · Guessed how to say: Eldoria"
    assert out["solo"][0]["chosen"] is False                     # one line: nothing to pick between lines
    assert out["wrong"] == f"{JOB}:1" and out["jobs"] == [JOB]


@needs_node
def test_a_line_made_again_adds_takes_and_keeps_the_ones_there(monkeypatch):
    first = {"job_id": JOB, "status": "done", "count": 2, "tries": tries(JOB, [(1, 1, 1, []), (2, 2, 1, [])])}
    again = {"job_id": AGAIN, "status": "done", "count": 2, "tries": tries(AGAIN, [(1, 1, 1, []), (2, 1, 2, [])])}
    out = _run(f"""let blocks = m.addTakes(m.newBlocks(['One line here.', 'Another line here.']), {json.dumps(first)}, {{ 1: 0, 2: 1 }});
      blocks = m.addTakes(blocks, {json.dumps(again)}, {{ 1: 1 }});
      blocks = m.chooseTake(blocks, 1, '{AGAIN}:2');
      console.log(JSON.stringify({{ blocks, titles: m.blockCards(blocks[1], null, true).map((c) => c.title),
        join: m.joinRequest(blocks, {{ gapMs: 250, name: ' Shop ' }}), jobs: m.jobsOnPage(blocks),
        problems: [m.joinProblem(blocks), m.joinProblem(blocks.slice(0, 1)), m.joinProblem(m.newBlocks(['a', 'b']))] }}))""",
               defaults(monkeypatch))
    first_block, second = out["blocks"]
    assert [t["job_id"] for t in second["takes"]] == [JOB, AGAIN, AGAIN] and len(first_block["takes"]) == 1
    assert first_block["making"] is False and out["titles"] == ["Take 1", "Take 2", "Take 3"]
    assert out["join"] == {"parts": [{"job_id": JOB, "try": 1}, {"job_id": AGAIN, "try": 2}], "gap_ms": 250, "name": "Shop"}
    assert out["jobs"] == [JOB, AGAIN]
    assert out["problems"] == [None, "There is only one line. Keep it instead.", "Line 1 has no take yet. Make it again first."]


@needs_node
def test_the_progress_line_says_where_it_is_and_how_it_ended(monkeypatch):
    out = _run("""const t = (n) => Array.from({ length: n }, (_, i) => ({ number: i + 1 }));
      console.log(JSON.stringify([m.voiceProgress(null), m.voiceProgress({ status: 'queued', count: 4 }),
        m.voiceProgress({ status: 'running', count: 4, tries: t(2) }), m.voiceProgress({ status: 'done', count: 4, tries: t(4) }),
        m.voiceProgress({ status: 'done', count: 6, tries: t(6) }, 3), m.voiceProgress({ status: 'done', count: 4, tries: t(3) }),
        m.voiceProgress({ status: 'error', error: 'It fell over.' }), m.voiceProgress({ status: 'cancelled', count: 4, tries: t(1) })]))""",
               defaults(monkeypatch))
    assert out[0] == "" and out[1] == "Loading the voice…" and out[2] == "Saying it: 2 of 4 made"
    assert out[3] == "Done. Play each one and keep what you like." and "Pick a take of each line" in out[4]
    assert "3 of 4 came out" in out[5] and out[6] == "It fell over." and out[7] == "Stopped. 1 of 4 made."


@needs_node
def test_the_voice_sheet_loads_as_a_module():
    """A slip in the file would only show when someone opens the sheet: import the real one."""
    url = (STUDIO / "voice-pane.js").as_uri()
    result = subprocess.run([NODE, "--input-type=module", "--eval",
                             f"const m = await import({json.dumps(url)}); console.log(Object.keys(m).join())"],
                            check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "openVoicePane"


def test_voice_opens_from_the_sound_sheets_kind_button_and_goes_back():
    app = (STUDIO / "app.js").read_text(encoding="utf-8")
    assert "from './voice-pane.js'" in app and "voice: openVoice" in app
    assert "openVoicePane({ stage: $('stage'), on: { kept: refreshSounds, back:" in app
    pane = (STUDIO / "sound-pane.js").read_text(encoding="utf-8")
    assert "on.voice?.()" in pane and "sheet === 'voice'" in pane
    voice = (STUDIO / "voice-pane.js").read_text(encoding="utf-8")
    assert "on.back?.()" in voice


def test_the_sheet_never_starts_a_download_and_only_talks_to_its_own_routes():
    pane = (STUDIO / "voice-pane.js").read_text(encoding="utf-8")
    assert "/api/setup" not in pane and "download" not in pane.lower().replace("download_size", "")
    assert 'href="./setup.html"' in pane and "voiceNotInstalledText" in pane
    posted = set(re.findall(r"post\(\s*[`']([^`'$]+)", pane))
    assert posted == {"/api/voice", "/api/voice/join", "/api/sound/"}
    assert set(re.findall(r"getJson\(\s*[`']([^`'$]+)", pane)) == {"/api/voice/defaults", "/api/sound/defaults", "/api/sound/kept", "/api/sound/"}


def test_there_is_no_way_to_hand_over_a_recording():
    """The consent rule on the page: preset voices only. No file box, no microphone."""
    shown = (STUDIO / "voice-pane.js").read_text(encoding="utf-8") + (STUDIO / "voice.js").read_text(encoding="utf-8")
    for door in ('type="file"', "getUserMedia", "MediaRecorder", "FormData", "ref_audio", "upload", "clone"):
        assert door not in shown, door


def test_the_words_on_screen_are_plain():
    """Studio text is for the user: options named by what they do, no engine names or audio jargon."""
    shown = (STUDIO / "voice-pane.js").read_text(encoding="utf-8") + (STUDIO / "voice.js").read_text(encoding="utf-8")
    words = (" ".join(re.findall(r">([A-Z][^<>{}$=;()]*)<", shown)) + " "
             + " ".join(re.findall(r"['`]([A-Z][^'`\n]{8,})['`]", shown))).lower()
    for jargon in ("tts", "lufs", "sampler", "phoneme", "token", "kokoro", "qwen", "mlx", "seed", "wav", "prompt",
                   "provenance", "instruct", "khz", "preset"):
        assert jargon not in words, jargon
    for said in ("Say a <span>line", "How should it sound?", "Voice", "How it's said", "Language", "What to say",
                 "Make this line again", "Save as one", "In your Library", "Pick this one"):
        assert said in shown, said
    assert "Make ${" in shown


def test_the_sheet_has_its_styles():
    css = (STUDIO / "studio.css").read_text(encoding="utf-8")
    section = css.split("/* Voice sheet (voice-pane.js)")[1]
    for rule in (".voice-line", ".voice-join", ".try-card.chosen", ".sound-form select"):
        assert rule in section, rule
    assert "#fd6d14" not in section                                  # the studio's own colours, by name
