#!/usr/bin/env python3
"""Make music from a description with ACE-Step 1.5, levelled and with its licence record.

Runs inside the music engine's own environment (`vendor/music/.venv`, made by
`bootstrap_music.py`), because that is where MLX, PyTorch and the `ace-step` package live.
The studio starts it for "Music"; it also works by hand:

    vendor/music/.venv/bin/python scripts/music_generate.py \\
        --description "calm fantasy village theme, harp, flute, soft strings" --seconds 30
    vendor/music/.venv/bin/python scripts/music_generate.py \\
        --description "tense battle music, drums, low brass" --seconds 20 --loop --no-plan

It is instrumental unless you give `--lyrics`. `--plan` (the default) runs the song planner
first: it picks a tempo and a key and sketches the tune; `--no-plan` skips it for a quicker,
looser try. `--loop` makes a tune that plays round and round with no click.

It never downloads anything. The weights must already be on disk (Setup, or the bootstrap);
if they are not, it stops and says what is missing and how big it is. The engine would
fetch missing models by itself: this checks first, pins the process offline, and stops
instead. It is given words only: there is no way to hand it a recording to copy.

The engine reads its models from one folder and writes its own code files into it, so it
is given `vendor/music/checkpoints/`: links to the weights, never the download itself.

Each tune keeps its two channels, is trimmed, set to a steady average volume, checked, and
written with `sound.provenance.json` beside it. One that comes out the wrong length or
mostly silent is dropped. With `--count` above 1 the tries land in `try-1/`, `try-2/`...
under `--out`, and try n uses seed + n - 1.

Progress goes to standard output as one JSON object per line, which is what the studio reads.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "viewer")]

import music_api
import sound_api

from image_to_3dlab import sound_post, weight_gate

CHECKPOINTS = "checkpoints"
LENGTH_SLACK_S = 2.0           # the engine answers the length asked; further off than this is a fault
LEAST_SHARE = 0.5              # ...and so is a tune that is more than half silence


def say(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, separators=(",", ":"), ensure_ascii=False), flush=True)


def local_weights(engine: music_api.MusicEngine, model_dir: str | None = None,
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


def checkpoint_view(weights: Path, engine: music_api.MusicEngine, root: Path) -> Path:
    """`root`/checkpoints: the folder the engine reads its models from. Every weight file is a
    link to the one on disk; the engine's small code files (`*.py`) are fresh copies, because
    the engine overwrites those with its own, and that must never reach the download."""
    view = root / CHECKPOINTS
    for name in engine.needs:
        source, target = (weights / name).resolve(), view / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".py"):
            if target.is_symlink():
                target.unlink()
            shutil.copyfile(source, target)
        elif not (target.is_symlink() and target.resolve() == source):
            if target.is_symlink() or target.exists():
                target.unlink()
            target.symlink_to(source)
    return view


def planned_facts(found: Any) -> dict[str, Any] | None:
    """What the song planner chose, under plain names. None when it did not run."""
    if not isinstance(found, dict) or not found:
        return None
    names = {"bpm": "tempo_bpm", "keyscale": "key", "timesignature": "beats_in_a_bar",
             "caption": "description", "language": "language"}
    told = {plain: found[key] for key, plain in names.items() if found.get(key) not in (None, "", "N/A")}
    return told or None


class Composer:
    """ACE-Step 1.5 through its own package: words in, (channels, frames) out. Only
    text-to-music is ever asked for: the reference-audio entry points are not."""

    def __init__(self, handler: Any, planner: Any, api: Any):
        self.handler, self.planner, self.api = handler, planner, api

    def compose(self, request: dict[str, Any], *, seed: int, seconds: float,
                progress: Callable[[float], None] | None = None) -> tuple[Any, int, dict[str, Any]]:
        import numpy as np

        plan = bool(request["plan"])
        params = self.api.GenerationParams(
            task_type="text2music", caption=music_api.engine_prompt(request),
            lyrics=request.get("lyrics") or "[Instrumental]", instrumental=not request["singing"],
            vocal_language=request["language"], duration=float(seconds),
            inference_steps=request["steps"], seed=seed,
            thinking=plan, use_cot_metas=plan, use_cot_caption=plan, use_cot_language=plan,
            enable_normalization=False,          # the volume is ours to set (sound_post.clean_music)
        )
        config = self.api.GenerationConfig(batch_size=1, use_random_seed=False, seeds=[seed], audio_format="wav")

        def report(fraction: Any = 0.0, *args: Any, **kwargs: Any) -> None:
            if progress is not None:
                progress(float(fraction))

        result = self.api.generate_music(self.handler, self.planner, params, config, save_dir=None, progress=report)
        if not result.success or not result.audios:
            raise sound_post.SoundProblem(f"the engine could not make it ({result.error or 'no reason given'})")
        audio = result.audios[0]
        samples = np.asarray(audio["tensor"].detach().cpu().numpy(), dtype=np.float32)
        return samples, int(audio["sample_rate"]), {"planned": planned_facts((result.extra_outputs or {}).get("lm_metadata"))}


def load_composer(engine: music_api.MusicEngine, weights: Path, plan: bool, root: Path | None = None) -> Composer:
    """The models, from files on disk. The only place the engine's own package is imported."""
    root = root or engine.environment
    view = checkpoint_view(weights, engine, root)
    # the engine keeps its notes and compiled code under the folder it is started from
    os.environ.setdefault("ACESTEP_PROJECT_ROOT", str(root))
    os.environ.setdefault("HF_MODULES_CACHE", str(root / ".cache" / "hf_modules"))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from acestep import inference, model_downloader
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    turbo, planner_folder = engine.parts[0].folder, engine.parts[1].folder
    # the engine downloads what it thinks is missing: ask it first, with its own check, and stop
    if not (model_downloader.check_main_model_exists(view) and model_downloader.check_model_exists(turbo, view)):
        weight_gate.refuse_hub_download(backend=engine.label, route=engine.route, size=engine.size,
                                        detail="The engine does not find all its models in " + str(view))
    handler = AceStepHandler()
    message, ready = handler.initialize_service(project_root=str(root), config_path=turbo, device="auto")
    if not ready:
        raise SystemExit(f"{engine.label} did not start: {str(message).splitlines()[0]}")
    planner = LLMHandler()
    if plan:
        message, ready = planner.initialize(checkpoint_dir=str(view), lm_model_path=planner_folder,
                                            backend="mlx", device="auto")
        if not ready:
            raise SystemExit(f"{engine.label}'s song planner did not start: {str(message).splitlines()[0]}")
    return Composer(handler, planner, inference)


def make_one(composer: Any, engine: music_api.MusicEngine, request: dict[str, Any], number: int,
             folder: Path, tell: Callable[..., None] = say) -> dict[str, Any]:
    """Compose, level, check and write try `number`, with its record. Returns its facts."""
    started = time.monotonic()
    seed = sound_api.try_seed(request["seed"], number)
    looped = bool(request.get("loop"))
    ask = (sound_post.seconds_for_music_loop(request["seconds"], engine.engine_max_seconds) if looped
           else request["seconds"])
    raw, rate, told = composer.compose(
        request, seed=seed, seconds=ask,
        progress=lambda fraction: tell("step", number=number, step=int(round(100 * min(1.0, max(0.0, fraction)))),
                                       total=100))
    frames = sound_post.to_frames(raw, channels_first=True)
    got = frames.shape[0] / max(1, rate)
    if abs(got - ask) > LENGTH_SLACK_S:
        raise sound_post.SoundProblem(f"it came out {got:.0f} s long, not the {ask:.0f} s asked, so it was dropped")
    points = None
    if looped:
        sound, post = sound_post.clean_music_loop(frames, rate, request["seconds"])
        points = (post["loop"]["start_sample"], post["loop"]["end_sample"])
    else:
        sound, post = sound_post.clean_music(frames, rate)
    facts = sound_post.inspect(sound, sound_post.RATE, want_channels=engine.channels)   # before it touches the disk
    if facts["seconds"] < LEAST_SHARE * request["seconds"]:
        raise sound_post.SoundProblem(f"only {facts['seconds']:.0f} s of it was music, so it was dropped")
    wav = sound_post.write_wav(folder / sound_api.SOUND_FILE, sound, loop=points)
    facts = sound_post.inspect_wav(wav, want_channels=engine.channels)
    if points and sound_post.read_loop(wav) != {"start_sample": points[0], "end_sample": points[1]}:
        raise sound_post.SoundProblem("the loop was not written into the file")
    planned = told.get("planned") if request["plan"] else None
    sound_api.write_sidecar(wav, music_api.sidecar(engine=engine, request=request, number=number, wav=wav,
                                                   facts=facts, post=post, planned=planned,
                                                   elapsed=time.monotonic() - started))
    return {"number": number, "seed": seed, "file": str(wav), "seconds": facts["seconds"],
            "peak": facts["peak"], "level_db": post["loudness"]["measured"],
            "bars": sound_post.wave_bars(sound), "took": round(time.monotonic() - started, 1),
            **({"planned": planned} if planned else {})}


def make_batch(composer: Any, engine: music_api.MusicEngine, request: dict[str, Any], out: Path,
               tell: Callable[..., None] = say) -> list[dict[str, Any]]:
    """Every try of one request, one after another. A try that comes out silent, broken or
    the wrong length is reported and skipped; the rest still get made."""
    made = []
    for number in range(1, request["count"] + 1):
        folder = sound_api.try_folder(out, number, request["count"])
        try:
            facts = make_one(composer, engine, request, number, folder, tell)
        except sound_post.SoundProblem as exc:
            tell("problem", number=number, error=f"Try {number}: {exc}.")
            continue
        made.append(facts)
        tell("try", **facts)
    return made


def main(argv: list[str] | None = None,
         load: Callable[[music_api.MusicEngine, Path, bool], Any] = load_composer) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--description", required=True, help="The music: style, mood, instruments.")
    parser.add_argument("--lyrics", help="Words to sing. Left out, the music is instrumental.")
    parser.add_argument("--language", help="The language of the words, as a code: en, es, ja... Default: unknown.")
    parser.add_argument("--seconds", type=float, help="How long, in seconds (10 to 240). Default 60.")
    parser.add_argument("--loop", action="store_true",
                        help="Make it play round and round with no click. Instrumental only.")
    parser.add_argument("--plan", action=argparse.BooleanOptionalAction, default=True,
                        help="Plan the song first: tempo, key, structure (default). --no-plan is quicker and looser.")
    parser.add_argument("--count", type=int, help="How many tries to make (1 to 4). Default 2.")
    parser.add_argument("--seed", type=int, help="Try 1's seed; try n uses seed + n - 1. Random if left out.")
    parser.add_argument("--name", help="A name for the output folder. Default: taken from the description.")
    parser.add_argument("--out", type=Path, help="Folder to write into. Default: output/sounds/<name>__<time>/.")
    parser.add_argument("--engine", default=music_api.ACE_STEP.id, choices=sorted(music_api.ENGINES))
    parser.add_argument("--model-dir", help="A folder that already holds the weights, instead of the "
                                            "Hugging Face cache.")
    args = parser.parse_args(argv)

    engine = music_api.pick_engine(args.engine)
    try:
        request = music_api.clean_request(
            {"prompt": args.description, "lyrics": args.lyrics, "language": args.language, "seconds": args.seconds,
             "loop": args.loop, "plan": args.plan, "count": args.count, "seed": args.seed, "name": args.name}, engine)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    # before the models are loaded, and before anything could reach for the network
    weights = local_weights(engine, args.model_dir)
    out = args.out or sound_api.OUTPUT_ROOT / f"{request['name']}__{time.strftime('%Y%m%d-%H%M%S')}"

    say("loading", engine=engine.id)
    composer = load(engine, weights, request["plan"])
    made = make_batch(composer, engine, request, out)
    if not made:
        print("No try came out as usable music. Try other words, or another length.")
        return 1
    say("done", tries=len(made), out=str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
