#!/usr/bin/env python3
"""Make a sound effect from a description with MOSS-SoundEffect v2.0, cleaned up and with its licence record.

Runs inside the sound engine's own environment (`vendor/moss-sfx/.venv`, made by
`bootstrap_sound_effects.py`), because that is where MLX and `moss-sfx-mlx` live. The studio
starts it for "Make a sound"; it also works by hand:

    vendor/moss-sfx/.venv/bin/python scripts/sound_generate.py \\
        --prompt "sword striking a wooden shield, one hit, dry" --seconds 1.5 --leave-out music

It never downloads anything. The weights must already be on disk (Setup, or the bootstrap);
if they are not, it stops and says what is missing and how big it is.

Each sound is trimmed, faded, held under full scale and checked before it is written
(`image_to_3dlab/sound_post.py`), then gets `sound.provenance.json` beside it. With
`--count` above 1 the tries land in `try-1/`, `try-2/`... under `--out`, and try n uses
seed + n - 1.

Progress goes to standard output as one JSON object per line, which is what the studio reads.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "viewer")]

import sound_api

from image_to_3dlab import sound_post, weight_gate


def say(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, separators=(",", ":")), flush=True)


def local_weights(engine: sound_api.SoundEngine, model_dir: str | None = None,
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


def load_pipeline(weights: Path):
    """The model, from files on disk. The only place the engine's own package is imported."""
    from moss_sfx_mlx.pipeline_mlx import MossSoundEffectPipeline

    return MossSoundEffectPipeline.from_pretrained(str(weights))


def step_reporter(number: int, tell: Callable[..., None]) -> Callable[..., Any]:
    """What the engine calls to wrap its step loop (it uses tqdm by default): the same
    steps, each one reported as it finishes."""
    def wrap(steps, *args, **kwargs):
        total = len(steps) if hasattr(steps, "__len__") else kwargs.get("total")
        for done, step in enumerate(steps, 1):
            yield step
            tell("step", number=number, step=done, total=total)
    return wrap


def generate(pipe, request: dict[str, Any], seed: int, number: int,
             tell: Callable[..., None] = say) -> tuple[Any, int]:
    """One raw sound from the engine: ((channels, frames) samples, sample rate)."""
    import numpy as np

    audio = pipe(
        prompt=request["prompt"],
        seconds=request["seconds"],
        num_inference_steps=request["steps"],
        cfg_scale=request["guidance"],
        seed=seed,
        negative_prompt=", ".join(request.get("leave_out") or []),
        progress_bar_cmd=step_reporter(number, tell),
    )
    # (batch, channels, frames): one prompt in, so one batch out
    return np.asarray(audio, dtype=np.float32)[0], int(getattr(pipe, "sample_rate", 0) or 48_000)


def make_one(pipe, engine: sound_api.SoundEngine, request: dict[str, Any], number: int,
             folder: Path, tell: Callable[..., None] = say) -> dict[str, Any]:
    """Generate, clean, check and write try `number`, with its record. Returns its facts."""
    started = time.monotonic()
    seed = sound_api.try_seed(request["seed"], number)
    raw, rate = generate(pipe, request, seed, number, tell)
    sound, post = sound_post.clean(raw, rate, channels_first=True)
    sound_post.inspect(sound, sound_post.RATE, want_channels=engine.channels)   # before it touches the disk
    wav = sound_post.write_wav(folder / sound_api.SOUND_FILE, sound)
    facts = sound_post.inspect_wav(wav, want_channels=engine.channels)
    record = sound_api.sidecar(engine=engine, request=request, number=number, wav=wav, facts=facts,
                               post=post, elapsed=time.monotonic() - started)
    sound_api.write_sidecar(wav, record)
    return {"number": number, "seed": seed, "file": str(wav), "seconds": facts["seconds"],
            "peak": facts["peak"], "bars": sound_post.wave_bars(sound)}


def make_batch(pipe, engine: sound_api.SoundEngine, request: dict[str, Any], out: Path,
               tell: Callable[..., None] = say) -> list[dict[str, Any]]:
    """Every try of one request, one after another. A try that comes out silent or broken
    is reported and skipped; the rest still get made."""
    made = []
    for number in range(1, request["count"] + 1):
        folder = sound_api.try_folder(out, number, request["count"])
        try:
            facts = make_one(pipe, engine, request, number, folder, tell)
        except sound_post.SoundProblem as exc:
            tell("problem", number=number, error=f"Try {number}: {exc}.")
            continue
        made.append(facts)
        tell("try", **facts)
    return made


def main(argv: list[str] | None = None, load: Callable[[Path], Any] = load_pipeline) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompt", required=True, help="What the sound is. One thing happening, close up.")
    parser.add_argument("--seconds", type=float, help="How long, in seconds (0.5 to 30).")
    parser.add_argument("--leave-out", action="append", default=[], metavar="THING",
                        help="Something the sound must not have, like music. Repeat for more.")
    parser.add_argument("--count", type=int, default=1, help="How many tries to make (1 to 4).")
    parser.add_argument("--seed", type=int, help="Try 1's seed; try n uses seed + n - 1. Random if left out.")
    parser.add_argument("--steps", type=int, help="Denoising steps. More is slower.")
    parser.add_argument("--name", help="A name for the output folder. Default: taken from the prompt.")
    parser.add_argument("--out", type=Path, help="Folder to write into. Default: output/sounds/<name>__<time>/.")
    parser.add_argument("--engine", default=sound_api.MOSS.id, choices=sorted(sound_api.ENGINES))
    parser.add_argument("--model-dir", help="A folder that already holds the weights, instead of the "
                                            "Hugging Face cache.")
    args = parser.parse_args(argv)

    engine = sound_api.pick_engine(args.engine)
    try:
        request = sound_api.clean_request(
            {"prompt": args.prompt, "seconds": args.seconds, "leave_out": args.leave_out,
             "count": args.count, "seed": args.seed, "steps": args.steps, "name": args.name}, engine)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    # before the model is loaded, and before anything could reach for the network
    weights = local_weights(engine, args.model_dir)
    out = args.out or sound_api.OUTPUT_ROOT / f"{request['name']}__{time.strftime('%Y%m%d-%H%M%S')}"

    say("loading", engine=engine.id)
    pipe = load(weights)
    made = make_batch(pipe, engine, request, out)
    if not made:
        print("No try came out as a usable sound. Try other words, or a longer length.")
        return 1
    say("done", tries=len(made), out=str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
