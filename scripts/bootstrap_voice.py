#!/usr/bin/env python3
"""Install a voice engine for the studio's Voice lines: Kokoro or Qwen3-TTS, both Apache-2.0.

You say which one, and nothing happens until you say yes:

    python scripts/bootstrap_voice.py --engine kokoro          # says what it wants, then asks
    python scripts/bootstrap_voice.py --engine qwen --yes      # for the studio and for agents
    python scripts/bootstrap_voice.py --engine qwen --check    # exit 0 if installed, 1 if not
    python scripts/bootstrap_voice.py --engine qwen --verify   # ...and its big files are the ones we tested

- `kokoro` ("Clear and quick"): Kokoro 82M, 0.34 GB. Reads plainly in about a second. English.
- `qwen` ("With feeling"): Qwen3-TTS 1.7B CustomVoice, 8-bit, 3.08 GB. Can shout or whisper,
  ten languages.

Each needs two things:

1. One Python environment for both, `vendor/voice/.venv`, with **mlx-audio** (MIT), which runs
   them, and every package pinned to an exact version. Audio packages stay out of the main
   environment, which pins numpy below 2.
2. Its weights in the Hugging Face cache, at one pinned revision. Files already there are not
   fetched again. Each large file is checked against its SHA-256.

Kokoro also needs something to turn words into sounds: **misaki** (Apache-2.0), which uses
spaCy (MIT), spaCy's small English model (MIT, 13 MB, fetched from spaCy's GitHub releases)
and num2words (LGPL-2.1, used as it comes). The usual helper for words in no dictionary,
**espeak-ng, is GPL-3 and is not installed**: neither it nor phonemizer. Our own letter rules
stand in for it (`image_to_3dlab/voice_text.py`), which is also why Kokoro here speaks English
only. Qwen3-TTS needs none of this.

`AGENTS.md`: a download path must name the backend, name the route, state the size, and
require an affirmative answer. This prints all of that and stops, unless `--yes` is given
for non-interactive use. Defaulting to yes is not allowed, so it does not.

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

import voice_api

from image_to_3dlab import host, sound_post, weight_gate

PYTHON_VERSION = "3.11"
# Everything in the environment, at the versions real lines were made with on 2026-11-20
# (`uv pip freeze`). mlx-audio 0.6.7 is the one whose Kokoro and Qwen3-TTS code we read.
PACKAGES = [
    "mlx-audio==0.6.7", "mlx==0.32.4", "mlx-metal==0.32.4", "numpy==2.4.6", "transformers==5.23.0",
    "tokenizers==0.23.3", "huggingface-hub==2.4.0", "hf-xet==1.8.0", "safetensors==0.8.0",
    "sentencepiece==0.2.3", "scipy==1.17.1", "miniaudio==1.73", "sounddevice==0.5.6",
    "misaki==0.9.4", "addict==2.4.0", "spacy==3.8.18", "num2words==0.5.14", "docopt==0.6.2",
    "spacy-legacy==3.0.12", "spacy-loggers==1.0.5", "thinc==8.3.14", "blis==1.3.4", "srsly==2.5.6",
    "catalogue==2.0.10", "confection==1.3.4", "cymem==2.0.16", "murmurhash==1.0.16", "preshed==3.0.15",
    "wasabi==1.1.3", "weasel==1.0.1", "cloudpathlib==0.26.0", "smart-open==9.0.6", "wrapt==2.5.2",
    "typer==0.29.2", "click==8.5.1", "rich==15.0.0", "shellingham==1.5.4", "markdown-it-py==4.3.0",
    "mdurl==0.1.2", "pygments==2.21.0", "pydantic==2.14.4", "pydantic-core==2.50.5",
    "annotated-types==0.8.0", "annotated-doc==0.0.7", "typing-extensions==4.16.0",
    "typing-inspection==0.4.5", "jinja2==3.1.6", "markupsafe==3.0.4", "pyyaml==6.0.3",
    "regex==2026.11.19", "requests==2.34.2", "urllib3==2.8.0", "certifi==2026.10.23",
    "charset-normalizer==3.5.2", "idna==3.20", "httpx==0.28.1", "httpcore==1.0.9", "httpx2==2.16.2",
    "httpcore2==2.16.2", "h11==0.16.0", "anyio==4.16.0", "truststore==0.10.5", "filelock==4.2.12",
    "fsspec==2026.9.0", "packaging==26.3", "tqdm==4.70.1", "cffi==2.1.1", "pycparser==3.12",
    "setuptools==84.2.1",
]
PACKAGES_SIZE = "about 0.5 GB"          # measured: 518 MB on disk
# Never to be in this environment: both are GPL-3, and both are what Kokoro guides tell you
# to install. `no_gpl` checks the list above; a test checks it too.
GPL = ("phonemizer", "phonemizer-fork", "espeakng-loader", "espeakng", "py-espeak-ng")
# spaCy's small English model: a tagger misaki reads each sentence with. A wheel on spaCy's
# GitHub releases, not on PyPI. Left alone, misaki would fetch it by itself on first use.
READER_NAME = "en_core_web_sm 3.8.0"
READER_URL = ("https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/"
              "en_core_web_sm-3.8.0-py3-none-any.whl")
READER_BYTES = 12_806_118
READER_SHA256 = "1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85"
READER = f"en_core_web_sm @ {READER_URL}#sha256={READER_SHA256}"

# Every file an engine needs from its repository at its pinned revision, with the bytes
# Hugging Face lists. The large ones carry their SHA-256 and are checked; the small ones are
# fixed by the revision itself. Kokoro: the model and its 28 English voices, not the other
# 26 voices, the samples or the PyTorch copies.
FILES: dict[str, list[tuple[str, int, str | None]]] = {
    "kokoro": [
        ("kokoro-v1_0.safetensors", 327_115_152, "4e9ecdf03b8b6cf906070390237feda473dc13327cb8d56a43deaa374c02acd8"),
        ("config.json", 2_351, None),
        *((f"voices/{voice.id}.safetensors", 522_320, None) for voice in voice_api.KOKORO.voices),
    ],
    "qwen": [
        ("model.safetensors", 2_393_308_931, "b696f4bf45f54497b6de758981e76f1410bd68af05a657477dee466b0202df1a"),
        ("speech_tokenizer/model.safetensors", 682_293_092,
         "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),
        ("vocab.json", 2_776_833, None),
        ("merges.txt", 1_671_839, None),
        ("model.safetensors.index.json", 71_786, None),
        ("tokenizer_config.json", 7_344, None),
        ("config.json", 6_058, None),
        ("generation_config.json", 245, None),
        ("preprocessor_config.json", 127, None),
        ("speech_tokenizer/config.json", 2_336, None),
        ("speech_tokenizer/configuration.json", 76, None),
        ("speech_tokenizer/preprocessor_config.json", 234, None),
    ],
}

LICENCE = {
    "kokoro": (
        "Apache-2.0 (Kokoro 82M, its MLX conversion and its voices). mlx-audio, which runs it, is MIT.\n"
        "  The lines you make are yours, commercial use included.\n"
        "  The voices are preset ones that come with the model. It cannot copy a person's voice.\n"
        "  Words to sounds: misaki (Apache-2.0), spaCy and its small English model (MIT),\n"
        "  num2words (LGPL-2.1, used as it comes). espeak-ng and phonemizer (GPL-3) are NOT\n"
        "  installed; our own letter rules say words that are in no dictionary. English only.\n"
        "  https://huggingface.co/hexgrad/Kokoro-82M"
    ),
    "qwen": (
        "Apache-2.0 (Qwen3-TTS and its MLX conversion), with no region limits. mlx-audio, which runs it, is MIT.\n"
        "  The lines you make are yours, commercial use included.\n"
        "  The voices are nine preset speakers. This model cannot copy a person's voice.\n"
        "  The model's training data is not published.\n"
        "  https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
    ),
}


def total_bytes(engine: voice_api.VoiceEngine) -> int:
    return sum(size for _, size, _ in FILES[engine.id])


def no_gpl(packages: list[str] | None = None) -> list[str]:
    """The GPL packages in `packages` (the list above, left out). Must be empty."""
    packages = PACKAGES if packages is None else packages
    return [spec for spec in packages if spec.split("==")[0].split(" ")[0].lower() in GPL]


def needs_reader(engine: voice_api.VoiceEngine) -> bool:
    return any(name == "en_core_web_sm" for name, _ in engine.packages)


def announcement(engine: voice_api.VoiceEngine) -> str:
    """Exactly what is about to be fetched, before anything is."""
    files = FILES[engine.id]
    lines = ["", "About to install:", "",
             f"  backend:  {engine.label} (voice lines: \"{engine.choice}\")",
             f"  route:    {engine.route}, Apple Silicon only",
             f"  packages: {PACKAGES_SIZE} of Python packages -> vendor/voice/.venv/",
             f"              mlx-audio 0.6.7 and {len(PACKAGES) - 1} more, each pinned to one version",
             "              (one environment for both voice engines: fetched once)"]
    if needs_reader(engine):
        lines += [f"  reader:   {READER_BYTES / 1e6:.0f} MB, spaCy's small English model ({READER_NAME}, MIT)",
                  "              from github.com/explosion/spacy-models, checked against its SHA-256"]
    lines += [f"  weights:  {total_bytes(engine) / 1e9:.2f} GB total ->  Hugging Face cache",
              f"              {engine.repo} at revision {engine.revision[:12]}",
              "              (files already there are not fetched again)"]
    for name, size, checked in files:
        if checked:
            lines.append(f"              {size / 1e9:>5.2f} GB  {name}")
    lines += ["", "  licence: " + LICENCE[engine.id], ""]
    return "\n".join(lines)


def install_environment(engine: voice_api.VoiceEngine,
                        runner: Callable[..., object] = subprocess.check_call,
                        root: Path | None = None) -> None:
    """The voice engines' own venv, then every pinned package into it. Kokoro also gets its
    English reader, by its exact address and hash."""
    if no_gpl():
        raise SystemExit("A GPL package is in the voice package list: " + ", ".join(no_gpl()))
    uv = host.find_uv()
    if uv is None:
        raise SystemExit("uv is not installed. See https://docs.astral.sh/uv/ and run this again.")
    root = root or engine.environment
    root.mkdir(parents=True, exist_ok=True)
    python = host.venv_python(root)
    if not python.exists():
        runner([uv, "venv", str(root / ".venv"), "--python", PYTHON_VERSION])
    runner([uv, "pip", "install", "--python", str(python), *PACKAGES])
    if needs_reader(engine):
        runner([uv, "pip", "install", "--python", str(python), "--no-deps", READER])


def check_file(name: str, path: Path, expected: str) -> None:
    """A cached copy that does not match is never deleted (another tool may share the
    cache); the user is told instead."""
    actual = sound_post.sha256_file(path)
    if actual != expected:
        raise SystemExit(f"{name} at {path} is not the file we tested (SHA-256 {actual}, "
                         f"expected {expected}). Delete it and run setup again.")


def fetch_weights(engine: voice_api.VoiceEngine, download: Callable[..., str]) -> None:
    """Each file at the pinned revision; the large ones checked against their SHA-256."""
    for name, size, expected in FILES[engine.id]:
        if expected:
            print(f"\nFetching {name} ({size / 1e9:.2f} GB) from {engine.repo}...", flush=True)
        path = Path(download(repo_id=engine.repo, filename=name, revision=engine.revision))
        if expected:
            print(f"  {path}\n  Checking it is the file we tested...", flush=True)
            check_file(name, path, expected)


def install_weights(engine: voice_api.VoiceEngine) -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise SystemExit("huggingface_hub is not installed. uv sync") from exc
    fetch_weights(engine, hf_hub_download)


def verify(engine: voice_api.VoiceEngine, hub: Path | None = None) -> list[str]:
    """What is wrong with the weights already on this disk: files missing, or large files
    that are not the ones we tested. Reads only; fetches nothing. Empty when all is well."""
    snapshot = weight_gate.snapshot_dir(engine.repo, hub)
    if snapshot is None:
        return [f"its model files ({engine.size})"]
    wrong = []
    for name, _, expected in FILES[engine.id]:
        path = snapshot / name
        if not path.exists():
            wrong.append(f"{name} is missing")
        elif expected and sound_post.sha256_file(path) != expected:
            wrong.append(f"{name} is not the file we tested")
    if snapshot.name != engine.revision and len(snapshot.name) == 40:
        wrong.append(f"the files are revision {snapshot.name[:12]}, not {engine.revision[:12]}")
    return wrong


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", required=True, choices=sorted(voice_api.ENGINES),
                        help="Which one. kokoro: clear and quick, 0.34 GB. qwen: with feeling, 3.08 GB.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation. For the studio and for scripts.")
    parser.add_argument("--check", action="store_true",
                        help="Say whether it is installed, and download nothing.")
    parser.add_argument("--verify", action="store_true",
                        help="Check the model files on this disk against the ones we tested. Downloads nothing.")
    args = parser.parse_args(argv)
    engine = voice_api.ENGINES[args.engine]
    if args.verify:
        wrong = verify(engine)
        print(f"{engine.label}: its model files are the ones we tested." if not wrong
              else f"{engine.label}: " + "; ".join(wrong) + ".")
        return 1 if wrong else 0
    if args.check:
        absent = voice_api.missing(engine)
        print(f"{engine.label} is installed." if not absent
              else f"{engine.label} is not installed. Missing: " + ", ".join(absent))
        return 1 if absent else 0
    if host.host_platform() != host.APPLE:
        print(f"{engine.label} here runs on MLX, which needs an Apple Silicon Mac. Nothing downloaded.")
        return 1
    print(announcement(engine))
    if not args.yes:
        # Non-interactive without --yes must not silently proceed, and must not hang
        # waiting on a stdin nobody is attached to.
        if not sys.stdin or not sys.stdin.isatty():
            print("Refusing to download without --yes when there is nobody to ask.")
            return 1
        if input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Nothing downloaded.")
            return 1
    install_environment(engine)
    install_weights(engine)
    print("\nDone. In the studio, press Sound, then Voice, and type a line to say.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
