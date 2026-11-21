#!/usr/bin/env python3
"""Install ACE-Step 1.5, the studio's music engine, MIT throughout.

Nothing happens until you say yes:

    python scripts/bootstrap_music.py            # says what it wants, then asks
    python scripts/bootstrap_music.py --yes      # for the studio and for agents
    python scripts/bootstrap_music.py --check    # exit 0 if installed, 1 if not
    python scripts/bootstrap_music.py --verify   # ...and its big files are the ones we tested

It needs two things:

1. Its own Python environment, `vendor/music/.venv`: the `ace-step` package (MIT, from its
   makers' repository at one commit, since it is not on PyPI) and what it needs to make
   music, each pinned to an exact version. PyTorch and MLX stay out of the main environment.
   The package lists far more than this (a web page, training tools, other download sites);
   none of that is installed, because making music does not use it.
2. The weights, 10.1 GB, in the Hugging Face cache at one pinned revision. Four parts, all
   used: the music model, the song planner, the text reader and the sound decoder. Files
   already there are not fetched again. Each large file is checked against its SHA-256.

`AGENTS.md`: a download path must name the backend, name the route, state the size, and
require an affirmative answer. This prints all of that and stops, unless `--yes` is given
for non-interactive use. Defaulting to yes is not allowed, so it does not.

Apple Silicon only: this route runs the music model on MLX, Apple's own array library.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "viewer")]

import music_api

from image_to_3dlab import host, sound_post, weight_gate

ENGINE = music_api.ACE_STEP
PYTHON_VERSION = "3.11"
# The engine's own code, at the commit we read and wrote the runner against (2026-11-20).
PACKAGE_REPO = "https://github.com/ace-step/ACE-Step-1.5"
PACKAGE_COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"
PACKAGE = f"ace-step @ git+{PACKAGE_REPO}@{PACKAGE_COMMIT}"
# Everything else in the environment, at the versions real music was made with on 2026-11-20
# (`uv pip freeze`). The big ones are the versions the engine's own lock file names for a Mac.
# The engine is installed without its own list of needs (`--no-deps`): that list also has
# gradio, lightning, modelscope, tensorboard and more, which making music never imports.
PACKAGES = [
    "torch==2.10.0", "torchaudio==2.10.0", "mlx==0.30.6", "mlx-metal==0.30.6", "mlx-lm==0.29.1",
    "transformers==4.57.6", "tokenizers==0.22.2", "diffusers==0.37.1", "accelerate==1.12.0",
    "safetensors==0.7.0", "huggingface-hub==0.36.0", "hf-xet==1.8.0", "numpy==2.3.5",
    "vector-quantize-pytorch==1.27.20", "einops==0.8.2", "einx==0.4.3", "soundfile==0.13.1",
    "loguru==0.7.3", "sentencepiece==0.2.3", "protobuf==7.36.2", "psutil==7.2.2", "pillow==13.0.0",
    "tqdm==4.70.1", "regex==2026.11.19", "pyyaml==6.0.4", "jinja2==3.1.6", "markupsafe==3.0.4",
    "sympy==1.14.0", "mpmath==1.3.0", "networkx==3.6.1", "filelock==4.2.12", "fsspec==2026.9.0",
    "packaging==26.3", "typing-extensions==4.16.0", "requests==2.34.2", "urllib3==2.8.0",
    "certifi==2026.11.20", "charset-normalizer==3.5.2", "idna==3.20", "httpx==0.28.1", "httpcore==1.0.9",
    "h11==0.16.0", "anyio==4.16.0", "cffi==2.1.1", "pycparser==3.12", "frozendict==2.5.1",
    "importlib-metadata==10.2.0", "zipp==4.1.2", "lazy-importer==0.1.0",
]
PACKAGES_SIZE = "about 0.7 GB"          # measured: 726 MB on disk

# Every file the engine needs from the repository at `ENGINE.revision`, with the bytes
# Hugging Face lists. The large ones carry their SHA-256 and are checked; the small ones are
# fixed by the revision itself. All of the repository but its README.
FILES: list[tuple[str, int, str | None]] = [
    ("acestep-v15-turbo/model.safetensors", 4_787_825_604,
     "3f6e0797fad420a39bd33979eb6e840e30989e34a3794e843d23b60ec6e422d7"),
    ("acestep-v15-turbo/silence_latent.pt", 3_841_215,
     "a778e9dd942f5e8b2c09c55370782d318834432b03dabbcdf70e6ed49ad6358b"),
    ("acestep-v15-turbo/modeling_acestep_v15_turbo.py", 96_036, None),
    ("acestep-v15-turbo/configuration_acestep_v15.py", 13_130, None),
    ("acestep-v15-turbo/config.json", 1_968, None),
    ("acestep-5Hz-lm-1.7B/model.safetensors", 3_708_521_528,
     "f161689da73e5ecefa28ff780d51c2d92a00f056d021d7933c779ed5c6cd7db8"),
    ("acestep-5Hz-lm-1.7B/tokenizer.json", 24_321_939,
     "35af56c3f5cb3ea2cc578aa28a8937770981d504f183ac5c8c38baf4bbd4af4d"),
    ("acestep-5Hz-lm-1.7B/tokenizer_config.json", 14_072_925,
     "6cd70cdd89425971794f5235562edcc608b0629a6c4686ae51a8b8c8b8ba5e95"),
    ("acestep-5Hz-lm-1.7B/vocab.json", 2_776_833, None),
    ("acestep-5Hz-lm-1.7B/added_tokens.json", 2_217_787, None),
    ("acestep-5Hz-lm-1.7B/special_tokens_map.json", 1_824_199, None),
    ("acestep-5Hz-lm-1.7B/merges.txt", 1_671_853, None),
    ("acestep-5Hz-lm-1.7B/chat_template.jinja", 4_168, None),
    ("acestep-5Hz-lm-1.7B/config.json", 1_385, None),
    ("Qwen3-Embedding-0.6B/model.safetensors", 1_191_586_416,
     "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"),
    ("Qwen3-Embedding-0.6B/tokenizer.json", 11_423_705,
     "def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a"),
    ("Qwen3-Embedding-0.6B/vocab.json", 2_776_833, None),
    ("Qwen3-Embedding-0.6B/merges.txt", 1_671_853, None),
    ("Qwen3-Embedding-0.6B/tokenizer_config.json", 5_404, None),
    ("Qwen3-Embedding-0.6B/chat_template.jinja", 4_116, None),
    ("Qwen3-Embedding-0.6B/config.json", 1_359, None),
    ("Qwen3-Embedding-0.6B/added_tokens.json", 707, None),
    ("Qwen3-Embedding-0.6B/special_tokens_map.json", 613, None),
    ("vae/diffusion_pytorch_model.safetensors", 337_431_388,
     "da17edb604c40deaf09e9b24974e590d1ca83a374070e5d0884cfa4bed9a99b0"),
    ("vae/config.json", 425, None),
    ("config.json", 1_968, None),
]

LICENCE = (
    "MIT (ACE-Step 1.5's weights, all four parts, and the ace-step code that runs them).\n"
    "  The music you make is yours, commercial use included.\n"
    "  Its makers say it was trained on licensed music, royalty-free and public-domain music,\n"
    "  and audio made from MIDI. We have not checked that. They also warn that a tune can come\n"
    "  out sounding like an existing one by chance: listen before you ship.\n"
    "  It is given words only here: no recording is ever handed to it to copy.\n"
    "  The weights include two small Python files and one PyTorch data file, which the engine\n"
    "  loads. The data file is read as numbers only, and is checked against its SHA-256.\n"
    "  https://huggingface.co/ACE-Step/Ace-Step1.5"
)


def total_bytes() -> int:
    return sum(size for _, size, _ in FILES)


def total_gb() -> float:
    return total_bytes() / 1e9


def part_bytes(part: music_api.Part) -> int:
    return sum(size for name, size, _ in FILES if name.startswith(part.folder + "/"))


def installed() -> bool:
    return music_api.is_installed(ENGINE)


def announcement() -> str:
    """Exactly what is about to be fetched, before anything is."""
    lines = ["", "About to install:", "",
             f"  backend:  {ENGINE.label} (text to music)",
             f"  route:    {ENGINE.route}, Apple Silicon only",
             f"  packages: {PACKAGES_SIZE} of Python packages -> vendor/music/.venv/",
             f"              ace-step at commit {PACKAGE_COMMIT[:12]} ({PACKAGE_REPO})",
             f"              and {len(PACKAGES)} more, each pinned to one version",
             f"  weights:  {total_gb():.1f} GB total ->  Hugging Face cache",
             f"              {ENGINE.repo} at revision {ENGINE.revision[:12]}",
             "              (files already there are not fetched again)"]
    for part in ENGINE.parts:
        lines.append(f"              {part_bytes(part) / 1e9:>5.2f} GB  {part.label}: {part.does}")
    lines += ["", "  licence: " + LICENCE, ""]
    return "\n".join(lines)


def install_environment(runner: Callable[..., object] = subprocess.check_call, root: Path | None = None) -> None:
    """The engine's own venv, every pinned package into it, then the engine itself with no
    list of needs of its own, so nothing unpinned and nothing unused comes with it."""
    uv = host.find_uv()
    if uv is None:
        raise SystemExit("uv is not installed. See https://docs.astral.sh/uv/ and run this again.")
    root = root or ENGINE.environment
    root.mkdir(parents=True, exist_ok=True)
    python = host.venv_python(root)
    if not python.exists():
        runner([uv, "venv", str(root / ".venv"), "--python", PYTHON_VERSION])
    runner([uv, "pip", "install", "--python", str(python), *PACKAGES])
    runner([uv, "pip", "install", "--python", str(python), "--no-deps", PACKAGE])


def check_file(name: str, path: Path, expected: str) -> None:
    """A cached copy that does not match is never deleted (another tool may share the
    cache); the user is told instead."""
    actual = sound_post.sha256_file(path)
    if actual != expected:
        raise SystemExit(f"{name} at {path} is not the file we tested (SHA-256 {actual}, "
                         f"expected {expected}). Delete it and run setup again.")


def fetch_weights(download: Callable[..., str]) -> None:
    """Each file at the pinned revision; the large ones checked against their SHA-256."""
    for name, size, expected in FILES:
        if expected:
            print(f"\nFetching {name} ({size / 1e9:.2f} GB) from {ENGINE.repo}...", flush=True)
        path = Path(download(repo_id=ENGINE.repo, filename=name, revision=ENGINE.revision))
        if expected:
            print(f"  {path}\n  Checking it is the file we tested...", flush=True)
            check_file(name, path, expected)


def install_weights() -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise SystemExit("huggingface_hub is not installed. uv sync") from exc
    fetch_weights(hf_hub_download)


def verify(hub: Path | None = None) -> list[str]:
    """What is wrong with the weights already on this disk: files missing, or large files
    that are not the ones we tested. Reads only; fetches nothing. Empty when all is well."""
    snapshot = weight_gate.snapshot_dir(ENGINE.repo, hub)
    if snapshot is None:
        return [f"its model files ({ENGINE.size})"]
    wrong = []
    for name, _, expected in FILES:
        path = snapshot / name
        if not path.exists():
            wrong.append(f"{name} is missing")
        elif expected and sound_post.sha256_file(path) != expected:
            wrong.append(f"{name} is not the file we tested")
    if snapshot.name != ENGINE.revision and len(snapshot.name) == 40:
        wrong.append(f"the files are revision {snapshot.name[:12]}, not {ENGINE.revision[:12]}")
    return wrong


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation. For the studio and for scripts.")
    parser.add_argument("--check", action="store_true",
                        help="Say whether it is installed, and download nothing.")
    parser.add_argument("--verify", action="store_true",
                        help="Check the model files on this disk against the ones we tested. Downloads nothing.")
    args = parser.parse_args(argv)
    if args.verify:
        wrong = verify()
        print(f"{ENGINE.label}: its model files are the ones we tested." if not wrong
              else f"{ENGINE.label}: " + "; ".join(wrong) + ".")
        return 1 if wrong else 0
    if args.check:
        absent = music_api.missing(ENGINE)
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
    print("\nDone. In the studio, press Sound, then Music, and describe a tune.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
