#!/usr/bin/env python3
"""Install MOSS-SoundEffect v2.0, the studio's sound-effect engine, Apache-2.0 throughout.

Two things, and nothing until you say yes:

1. The engine's own Python environment in `vendor/moss-sfx/.venv`, with `moss-sfx-mlx`
   (the MLX port, Apache-2.0) at one pinned commit. Audio packages stay out of the main
   environment, which pins numpy below 2.
2. The 4-bit weights, about 6.4 GB, into the Hugging Face cache, at one pinned revision.
   Each large file is checked against its SHA-256 once it is down.

`AGENTS.md`: a download path must name the backend, name the route, state the size, and
require an affirmative answer. This prints all of that and stops, unless `--yes` is given
for non-interactive use. Defaulting to yes is not allowed, so it does not.

    python scripts/bootstrap_sound_effects.py          # says what it wants, then asks
    python scripts/bootstrap_sound_effects.py --yes    # for the studio and for agents
    python scripts/bootstrap_sound_effects.py --check  # exit 0 if installed, 1 if not

Apple Silicon only: MLX is Apple's own array library and runs nowhere else.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "viewer")]

import sound_api

from image_to_3dlab import host, sound_post

ENGINE = sound_api.MOSS
PYTHON_VERSION = "3.11"
# The MLX port, at the commit we read and wrote the runner against (2026-11-20). It is not
# on PyPI under a version we can pin, so it comes from its repository by commit.
PACKAGE_REPO = "https://github.com/xocialize/moss-soundeffect-mlx"
PACKAGE_COMMIT = "4e8e3e36d4d9c991d16a3e65a4b7099d406b9ed8"
PACKAGE = f"moss-sfx-mlx @ git+{PACKAGE_REPO}@{PACKAGE_COMMIT}"
# Not measured: MLX, transformers and what they bring. Stated so the yes covers it.
PACKAGES_SIZE = "about 0.5 GB"

# Every file of the weights repository at `ENGINE.revision`, with the bytes Hugging Face
# lists. The five large ones carry their SHA-256 and are checked after the download; the
# small text files are fixed by the revision itself.
FILES: list[tuple[str, int, str | None]] = [
    ("mlx/dit.safetensors", 831_262_348, "714d055e7d686d6c3f57611941671701f279e354be9bc29b6d08278bdbb1cc20"),
    ("mlx/vae.safetensors", 1_486_185_128, "158ab5fd08134598aec356e914bc147ccb6fac369f95042ca0560675a1b0ebdb"),
    ("text_encoder/model-00001-of-00002.safetensors", 3_441_185_608,
     "169ad53ec313c3a34b06c0809216e4fc072cce444a5d4ff2b59690d064130ed5"),
    ("text_encoder/model-00002-of-00002.safetensors", 622_329_984,
     "912becff8d60672aa8628ef08c05898d9adf17c2ad4ae3caf99b065622fdeff9"),
    ("tokenizer/tokenizer.json", 11_422_654, "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"),
    ("model_index.json", 686, None),
    ("scheduler/scheduler_config.json", 134, None),
    ("text_encoder/config.json", 726, None),
    ("text_encoder/generation_config.json", 239, None),
    ("text_encoder/model.safetensors.index.json", 25_605, None),
    ("tokenizer/merges.txt", 1_671_853, None),
    ("tokenizer/tokenizer_config.json", 9_732, None),
    ("tokenizer/vocab.json", 2_776_833, None),
    ("transformer/config.json", 311, None),
    ("vae/config.json", 169, None),
]

LICENCE = (
    "Apache-2.0 (MOSS-SoundEffect v2.0, its MLX conversion and the moss-sfx-mlx code).\n"
    "  The sounds you make are yours, commercial use included.\n"
    "  The model's training data is not published.\n"
    "  https://huggingface.co/OpenMOSS-Team/MOSS-SoundEffect-v2.0"
)


def total_bytes() -> int:
    return sum(size for _, size, _ in FILES)


def total_gb() -> float:
    return total_bytes() / 1e9


def installed() -> bool:
    return sound_api.is_installed(ENGINE)


def announcement() -> str:
    """Exactly what is about to be fetched, before anything is."""
    lines = ["", "About to install:", "",
             f"  backend:  {ENGINE.label} (text to sound effect)",
             f"  route:    {ENGINE.route}, Apple Silicon only",
             f"  packages: {PACKAGES_SIZE} of Python packages -> vendor/moss-sfx/.venv/",
             f"              moss-sfx-mlx at commit {PACKAGE_COMMIT[:12]} ({PACKAGE_REPO})",
             f"  weights:  {total_gb():.1f} GB total ->  Hugging Face cache",
             f"              {ENGINE.repo} at revision {ENGINE.revision[:12]}"]
    for name, size, checked in FILES:
        if checked:
            lines.append(f"              {size / 1e9:>5.2f} GB  {name}")
    lines += ["", "  licence: " + LICENCE, ""]
    return "\n".join(lines)


def install_environment(runner: Callable[..., object] = subprocess.check_call,
                        root: Path = ENGINE.environment) -> None:
    """The engine's own venv, then the pinned package into it."""
    uv = host.find_uv()
    if uv is None:
        raise SystemExit("uv is not installed. See https://docs.astral.sh/uv/ and run this again.")
    root.mkdir(parents=True, exist_ok=True)
    python = host.venv_python(root)
    if not python.exists():
        runner([uv, "venv", str(root / ".venv"), "--python", PYTHON_VERSION])
    runner([uv, "pip", "install", "--python", str(python), PACKAGE])


def fetch_weights(download: Callable[..., str]) -> None:
    """Each file at the pinned revision; the large ones checked against their SHA-256. A
    cached copy that does not match is never deleted (another tool may share the cache);
    the user is told instead."""
    for name, size, expected in FILES:
        if expected:
            print(f"\nFetching {name} ({size / 1e9:.2f} GB) from {ENGINE.repo}...", flush=True)
        path = Path(download(repo_id=ENGINE.repo, filename=name, revision=ENGINE.revision))
        if not expected:
            continue
        print(f"  {path}\n  Checking it is the file we tested...", flush=True)
        actual = sound_post.sha256_file(path)
        if actual != expected:
            raise SystemExit(f"{name} at {path} is not the file we tested (SHA-256 {actual}, "
                             f"expected {expected}). Delete it and run setup again.")


def install_weights() -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise SystemExit("huggingface_hub is not installed. uv sync") from exc
    fetch_weights(hf_hub_download)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation. For the studio and for scripts.")
    parser.add_argument("--check", action="store_true",
                        help="Say whether it is installed, and download nothing.")
    args = parser.parse_args(argv)
    if args.check:
        absent = sound_api.missing(ENGINE)
        print(f"{ENGINE.label} is installed." if not absent
              else f"{ENGINE.label} is not installed. Missing: " + ", ".join(absent))
        return 1 if absent else 0
    if host.host_platform() != host.APPLE:
        print(f"{ENGINE.label} here runs on MLX, which needs an Apple Silicon Mac. Nothing downloaded.")
        return 1
    print(announcement())
    if not args.yes:
        # Non-interactive without --yes must not silently proceed, and must not hang
        # waiting on a stdin nobody is attached to.
        if not sys.stdin or not sys.stdin.isatty():
            print("Refusing to download without --yes when there is nobody to ask.")
            return 1
        if input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Nothing downloaded.")
            return 1
    install_environment()
    install_weights()
    print("\nDone. In the studio, press Sound and describe what you want to hear.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
