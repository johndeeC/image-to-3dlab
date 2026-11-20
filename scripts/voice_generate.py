#!/usr/bin/env python3
"""Say a line out loud with Kokoro or Qwen3-TTS, levelled and with its licence record.

Runs inside the voice engines' own environment (`vendor/voice/.venv`, made by
`bootstrap_voice.py`), because that is where MLX and mlx-audio live. The studio starts it
for "Voice"; it also works by hand:

    vendor/voice/.venv/bin/python scripts/voice_generate.py --engine qwen --voice ryan \\
        --text "Halt! Who goes there?" --direction shout

`--engine kokoro` reads plainly and fast, in English. `--engine qwen` takes `--direction`
(angry, sad, whisper, shout, calm, excited, or your own few words) and ten languages.
The voice is one of the engine's own preset voices: it cannot be given a recording to copy.

It never downloads anything. The weights must already be on disk (Setup, or the bootstrap);
if they are not, it stops and says what is missing and how big it is. Kokoro's word reader
would fetch a small language model by itself the first time: this checks for it first and
stops instead.

Several sentences are several lines, each made `--count` times. The tries land in `try-1/`,
`try-2/`... under `--out` (line 1's takes first), and try n uses seed + n - 1. Each is
trimmed, brought to the voice level for how it is said, checked, and written with
`sound.provenance.json` beside it. A line that runs on past the longest it should take is
dropped, so a short line can never loop for a minute.

Progress goes to standard output as one JSON object per line, which is what the studio reads.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "viewer")]

import sound_api
import voice_api

from image_to_3dlab import sound_post, voice_text, weight_gate


def say(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, separators=(",", ":"), ensure_ascii=False), flush=True)


def local_weights(engine: voice_api.VoiceEngine, model_dir: str | None = None,
                  hub: Path | None = None) -> Path:
    """The engine's weights on this disk. Refuses, naming the engine, route and size, when
    they are not all there; and pins the process offline so nothing later can fetch them."""
    weights = Path(weight_gate.require_local(model_dir or engine.repo, backend=engine.label,
                                             route=engine.route, size=engine.size, hub=hub))
    absent = [name for name in engine.needs if not (weights / name).exists()]
    if absent:
        weight_gate.refuse_hub_download(
            backend=engine.label, route=engine.route, size=engine.size,
            detail="The download is not finished. Missing: " + ", ".join(absent))
    return weights


def require_packages(engine: voice_api.VoiceEngine,
                     find: Callable[[str], Any] = importlib.util.find_spec) -> None:
    """Stop before the engine can fetch a package for itself. Kokoro's word reader (misaki)
    downloads spaCy's small English model the first time it is used, if it is not there."""
    absent = [words for name, words in engine.packages if find(name) is None]
    if absent:
        raise SystemExit(f"{engine.label} is not fully installed. Missing: " + "; ".join(absent)
                         + ". Generation does not fetch it. Run: python scripts/bootstrap_voice.py "
                         f"--engine {engine.id}")


class LetterGuess:
    """Says a word that is in no dictionary from its letters (`voice_text.guess_phonemes`),
    and remembers which words it had to guess. It takes the place of misaki's espeak-ng
    fallback, which is GPL-3 and not installed."""

    def __init__(self, british: bool = False, version: str | None = None):
        self.british = british
        self.guessed: list[str] = []

    def __call__(self, token: Any) -> tuple[str | None, int | None]:
        word = str(getattr(token, "text", token))
        sounds = voice_text.guess_phonemes(word)
        if not sounds:
            return None, None
        self.guessed.append(word)
        return sounds, 1


def use_letter_guess() -> None:
    """Give mlx-audio's Kokoro code our `LetterGuess` where it asks for `misaki.espeak`. It
    imports that module for English whether or not it is used, and the real one needs
    espeak-ng and phonemizer (both GPL-3). With this in place neither is needed."""
    if "misaki.espeak" in sys.modules:
        return
    stand_in = types.ModuleType("misaki.espeak")
    stand_in.EspeakFallback = LetterGuess

    def no_other_languages(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Kokoro here speaks English only: its other languages need espeak-ng, which is GPL-3")

    stand_in.EspeakG2P = no_other_languages
    sys.modules["misaki.espeak"] = stand_in


class KokoroSpeaker:
    """Kokoro through mlx-audio. Nothing random in it: a line always comes out the same."""

    def __init__(self, model: Any):
        self.model = model

    def speak(self, text: str, *, voice: str, language: str, speed: float, direction: str | None,
              seed: int | None, most_tokens: int | None) -> tuple[Any, int, dict[str, Any]]:
        import numpy as np

        code = "b" if language == "en-gb" else "a"
        fallback = getattr(self.model._get_pipeline(code).g2p, "fallback", None)
        if isinstance(fallback, LetterGuess):
            fallback.guessed.clear()
        parts = [np.asarray(result.audio, dtype=np.float32).reshape(-1)
                 for result in self.model.generate(text=text, voice=voice, speed=speed, lang_code=code)]
        if not parts:
            raise sound_post.SoundProblem("the engine made no sound for this line")
        guessed = list(fallback.guessed) if isinstance(fallback, LetterGuess) else []
        return np.concatenate(parts), int(self.model.sample_rate), {"guessed": guessed}


class QwenSpeaker:
    """Qwen3-TTS CustomVoice through mlx-audio: a preset speaker, a language, and how to say
    it. Only `generate_custom_voice` is ever called: the cloning entry points are not."""

    def __init__(self, model: Any, seed_with: Callable[[int], None] | None = None):
        self.model = model
        self.seed_with = seed_with

    def speak(self, text: str, *, voice: str, language: str, speed: float, direction: str | None,
              seed: int | None, most_tokens: int | None) -> tuple[Any, int, dict[str, Any]]:
        import numpy as np

        if self.seed_with is not None and seed is not None:
            self.seed_with(seed)
        results = list(self.model.generate_custom_voice(
            text=text, speaker=voice, language=language, instruct=direction or None, max_tokens=most_tokens))
        if not results:
            raise sound_post.SoundProblem("the engine made no sound for this line")
        tokens = sum(int(getattr(result, "token_count", 0) or 0) for result in results)
        if most_tokens and tokens >= most_tokens:
            raise sound_post.SoundProblem("it ran on past the longest this line should take, so it was dropped")
        audio = np.concatenate([np.asarray(result.audio, dtype=np.float32).reshape(-1) for result in results])
        return audio, int(self.model.sample_rate), {"tokens": tokens}


def load_speaker(engine: voice_api.VoiceEngine, weights: Path) -> Any:
    """The model, from files on disk. The only place the engines' own package is imported."""
    if engine.id == voice_api.KOKORO.id:
        use_letter_guess()
    import mlx.core as mx
    from mlx_audio.tts.utils import load_model

    # the type is named: mlx-audio would otherwise guess it from the folder's name, a hash
    if engine.id == voice_api.KOKORO.id:
        return KokoroSpeaker(load_model(Path(weights), model_type="kokoro"))
    return QwenSpeaker(load_model(Path(weights), model_type="qwen3_tts"), mx.random.seed)


def make_one(speaker: Any, engine: voice_api.VoiceEngine, request: dict[str, Any], number: int,
             folder: Path) -> dict[str, Any]:
    """Say, level, check and write try `number`, with its record. Returns its facts."""
    started = time.monotonic()
    line, take = voice_api.line_of(number, request["takes"])
    text = request["spoken"][line - 1]
    direction = request.get("direction") or {}
    seed = sound_api.try_seed(request["seed"], number) if engine.seeded else None
    most = voice_text.speech_tokens(text, engine.tokens_per_second) if engine.tokens_per_second else None
    raw, rate, told = speaker.speak(text, voice=request["voice"], language=request["language"],
                                    speed=request["speed"], direction=direction.get("words"),
                                    seed=seed, most_tokens=most)
    sound, post = sound_post.clean_voice(raw, rate, direction.get("id"))
    facts = sound_post.inspect(sound, sound_post.RATE, want_channels=engine.channels)   # before it touches the disk
    if facts["seconds"] > voice_text.most_seconds(text) / max(0.1, request["speed"]):
        raise sound_post.SoundProblem("it ran on past the longest this line should take, so it was dropped")
    wav = sound_post.write_wav(folder / sound_api.SOUND_FILE, sound)
    facts = sound_post.inspect_wav(wav, want_channels=engine.channels)
    guessed = told.get("guessed") or []
    sound_api.write_sidecar(wav, voice_api.sidecar(
        engine=engine, request=request, number=number, wav=wav, facts=facts, post=post,
        elapsed=time.monotonic() - started, guessed=guessed))
    return {"number": number, "line": line, "take": take, "seed": seed, "file": str(wav),
            "seconds": facts["seconds"], "peak": facts["peak"], "level_db": post["loudness"].get("level"),
            "bars": sound_post.wave_bars(sound), "guessed": guessed,
            "took": round(time.monotonic() - started, 2)}


def make_batch(speaker: Any, engine: voice_api.VoiceEngine, request: dict[str, Any], out: Path,
               tell: Callable[..., None] = say) -> list[dict[str, Any]]:
    """Every take of every line, one after another. One that comes out silent, broken or
    far too long is reported and skipped; the rest still get made."""
    made = []
    for number in range(1, request["count"] + 1):
        folder = sound_api.try_folder(out, number, request["count"])
        try:
            facts = make_one(speaker, engine, request, number, folder)
        except sound_post.SoundProblem as exc:
            tell("problem", number=number, error=f"Try {number}: {exc}.")
            continue
        made.append(facts)
        tell("try", **facts)
    return made


def main(argv: list[str] | None = None, load: Callable[[voice_api.VoiceEngine, Path], Any] = load_speaker,
         find: Callable[[str], Any] = importlib.util.find_spec) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", default=voice_api.KOKORO.id, choices=sorted(voice_api.ENGINES),
                        help="kokoro: clear and quick, English. qwen: with feeling, ten languages.")
    parser.add_argument("--text", action="append", required=True,
                        help="What to say. Several sentences become several lines. Repeat for more.")
    parser.add_argument("--no-split", action="store_true",
                        help="Take each --text as one line, as typed, instead of splitting it into sentences.")
    parser.add_argument("--voice", help="One of the engine's own voices. Default: af_heart (kokoro), ryan (qwen).")
    parser.add_argument("--direction", help="How it is said (qwen only): angry, sad, whisper, shout, calm, "
                                            "excited, or your own few words.")
    parser.add_argument("--language", help="qwen: english, chinese, japanese, korean, german, french, spanish, "
                                           "italian, portuguese, russian. kokoro: follows the voice.")
    parser.add_argument("--speed", type=float, help="kokoro only: 0.7 (slow) to 1.4 (fast). Default 1.")
    parser.add_argument("--seed", type=int, help="qwen: try 1's seed; try n uses seed + n - 1. Random if left out.")
    parser.add_argument("--count", type=int, help="Tries of each line: 1 to 4 for qwen, 1 for kokoro "
                                                  "(it says a line the same way every time).")
    parser.add_argument("--name", help="A name for the output folder. Default: taken from the words.")
    parser.add_argument("--out", type=Path, help="Folder to write into. Default: output/sounds/<name>__<time>/.")
    parser.add_argument("--model-dir", help="A folder that already holds the weights, instead of the "
                                            "Hugging Face cache.")
    args = parser.parse_args(argv)

    engine = voice_api.pick_engine(args.engine)
    words = {"lines": args.text} if args.no_split else {"text": "\n".join(args.text)}
    try:
        request = voice_api.clean_request(
            {**words, "voice": args.voice, "direction": args.direction, "language": args.language,
             "speed": args.speed, "seed": args.seed, "count": args.count, "name": args.name}, engine)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    # before the model is loaded, and before anything could reach for the network
    weights = local_weights(engine, args.model_dir)
    require_packages(engine, find)
    out = args.out or sound_api.OUTPUT_ROOT / f"{request['name']}__{time.strftime('%Y%m%d-%H%M%S')}"

    say("loading", engine=engine.id)
    speaker = load(engine, weights)
    made = make_batch(speaker, engine, request, out)
    if not made:
        print("No try came out as a usable line. Try other words, or another voice.")
        return 1
    say("done", tries=len(made), out=str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
