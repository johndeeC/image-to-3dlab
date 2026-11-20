"""Tests for scripts/bootstrap_voice.py: what it says before a byte moves, consent, and pins.

The two installs (the environment and the weights) are always replaced here, so no test can
reach uv, the network or the real vendor/ folder.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "viewer")]

import backend_catalog
import bootstrap_voice as boot
import voice_api

REAL_FETCH = boot.fetch_weights
REAL_ENVIRONMENT = boot.install_environment
ENGINES = [voice_api.KOKORO, voice_api.QWEN]


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch):
    monkeypatch.setattr(boot, "install_environment", lambda engine: pytest.fail("reached the real package install"))
    monkeypatch.setattr(boot, "install_weights", lambda engine: pytest.fail("reached the real download"))
    monkeypatch.setattr(boot.host, "host_platform", lambda: boot.host.APPLE)


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.id)
def test_the_announcement_names_engine_route_size_and_licence(engine):
    text = boot.announcement(engine)
    assert engine.label in text and engine.route in text and f'"{engine.choice}"' in text
    assert f"{boot.total_bytes(engine) / 1e9:.2f} GB" in text and engine.size in text
    assert "Apache-2.0" in text and "commercial" in text and "MIT" in text and "mlx-audio 0.6.7" in text
    assert boot.PACKAGES_SIZE in text and "vendor/voice" in text
    assert engine.repo in text and engine.revision[:12] in text
    assert "cannot copy a person's voice" in text
    for name, size, checked in boot.FILES[engine.id]:
        if checked:
            assert name in text and f"{size / 1e9:.2f}" in text


def test_kokoros_announcement_names_the_reader_and_says_no_gpl():
    text = boot.announcement(voice_api.KOKORO)
    assert "13 MB" in text and "en_core_web_sm 3.8.0" in text and "github.com/explosion/spacy-models" in text
    assert "espeak-ng and phonemizer (GPL-3) are NOT" in text and "num2words (LGPL-2.1" in text
    assert "English only" in text
    other = boot.announcement(voice_api.QWEN)
    assert "en_core_web_sm" not in other and "training data is not published" in other


def test_nothing_gpl_is_installed_and_every_package_is_pinned():
    assert boot.no_gpl() == []
    assert boot.no_gpl(["mlx-audio==0.6.7", "phonemizer-fork==3.3.2", "espeakng-loader==0.2.4"]) == [
        "phonemizer-fork==3.3.2", "espeakng-loader==0.2.4"]
    assert all(spec.count("==") == 1 and spec.split("==")[1] for spec in boot.PACKAGES)
    names = [spec.split("==")[0] for spec in boot.PACKAGES]
    assert len(names) == len(set(names)) and {"mlx-audio", "mlx", "misaki", "spacy", "num2words"} <= set(names)
    assert "mlx-audio==0.6.7" in boot.PACKAGES
    assert boot.READER.startswith("en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/")
    assert boot.READER.endswith(f"#sha256={boot.READER_SHA256}") and len(boot.READER_SHA256) == 64


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.id)
def test_the_catalogue_card_quotes_the_same_repository_and_size(engine):
    card = backend_catalog.BY_ID[engine.setup_id]
    assert [w.source for w in card.weights] == [engine.repo]
    assert sum(w.bytes_expected for w in card.weights) == boot.total_bytes(engine)
    assert card.install == f"scripts/bootstrap_voice.py --engine {engine.id}" and card.kind == "sound"
    assert card.license_name == "Apache-2.0" and card.runs_on == (backend_catalog.APPLE,)
    assert card.license_url == engine.licence.license_url
    assert card.build_probes[0] == engine.python                # installed only with the voice environment
    assert f"{boot.total_bytes(engine) / 1e9:.2f} GB" == engine.size


def test_kokoros_card_is_not_installed_without_its_reader_and_says_what_it_cannot_do():
    card = backend_catalog.BY_ID["voice-kokoro"]
    assert card.build_probes[1].name == "en_core_web_sm" and "vendor/voice" in card.build_probes[1].as_posix()
    assert "GPL-3" in card.tradeoff and "English only" in card.tradeoff
    assert len(backend_catalog.BY_ID["voice-qwen"].build_probes) == 1


def test_an_engine_must_be_named(capsys):
    with pytest.raises(SystemExit):
        boot.main(["--yes"])                                   # no default: a download names its backend
    assert "--engine" in capsys.readouterr().err


def test_nothing_downloads_without_a_yes_and_nobody_to_ask(monkeypatch, capsys):
    monkeypatch.setattr(boot.sys, "stdin", io.StringIO(""))
    assert boot.main(["--engine", "qwen"]) == 1
    said = capsys.readouterr().out
    assert "Refusing to download" in said and "About to install" in said and "3.08 GB" in said


@pytest.mark.parametrize("answer", ["n", "", "maybe"])
def test_anything_but_a_typed_yes_downloads_nothing(monkeypatch, capsys, answer):
    monkeypatch.setattr(boot.sys, "stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})(""))
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    assert boot.main(["--engine", "kokoro"]) == 1
    assert "Nothing downloaded" in capsys.readouterr().out


def test_a_yes_installs_the_environment_then_the_weights_of_the_engine_named(monkeypatch):
    done = []
    monkeypatch.setattr(boot, "install_environment", lambda engine: done.append(("environment", engine.id)))
    monkeypatch.setattr(boot, "install_weights", lambda engine: done.append(("weights", engine.id)))
    assert boot.main(["--engine", "kokoro", "--yes"]) == 0 and done == [("environment", "kokoro"), ("weights", "kokoro")]
    monkeypatch.setattr(boot.sys, "stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})(""))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert boot.main(["--engine", "qwen"]) == 0 and done[2:] == [("environment", "qwen"), ("weights", "qwen")]


def test_a_machine_that_is_not_a_mac_is_told_so_and_downloads_nothing(monkeypatch, capsys):
    monkeypatch.setattr(boot.host, "host_platform", lambda: boot.host.NVIDIA)
    assert boot.main(["--engine", "qwen", "--yes"]) == 1
    assert "Apple Silicon" in capsys.readouterr().out


def test_check_only_looks(monkeypatch, capsys):
    monkeypatch.setattr(boot.voice_api, "missing", lambda engine: ["its own Python environment"])
    assert boot.main(["--engine", "kokoro", "--check"]) == 1
    assert "Kokoro 82M is not installed" in capsys.readouterr().out
    monkeypatch.setattr(boot.voice_api, "missing", lambda engine: [])
    assert boot.main(["--engine", "kokoro", "--check"]) == 0


def test_the_studio_setup_buttons_run_it_with_the_engine_and_yes():
    import download_api
    for engine in ENGINES:
        command = download_api.COMMANDS[engine.setup_id]      # the Setup page's own dialog is the asking
        assert command[1].endswith("bootstrap_voice.py") and command[2:] == ["--engine", engine.id, "--yes"]


def test_the_packages_and_the_reader_are_named_before_the_yes_until_they_are_installed(monkeypatch, tmp_path):
    import download_api
    moved = {e.id: dataclasses.replace(e, environment=tmp_path) for e in ENGINES}
    monkeypatch.setattr(boot.voice_api, "ENGINES", moved)
    packages, reader = download_api.also_fetches("voice-kokoro")
    assert packages["size"] == boot.PACKAGES_SIZE and packages["licence"] == "MIT" and "mlx-audio" in packages["what"]
    assert reader["size"] == "13 MB" and reader["licence"] == "MIT" and reader["from"] == boot.READER_URL
    assert [item["licence"] for item in download_api.also_fetches("voice-qwen")] == ["MIT"]
    python = moved["qwen"].python
    python.parent.mkdir(parents=True)
    python.write_text("")
    assert download_api.also_fetches("voice-qwen") == []
    (tmp_path / ".venv" / "lib" / "python3.11" / "site-packages" / "en_core_web_sm").mkdir(parents=True)
    assert download_api.also_fetches("voice-kokoro") == []


def test_the_environment_is_its_own_and_only_kokoro_gets_the_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(boot.host, "find_uv", lambda: "/bin/uv")
    ran = []
    REAL_ENVIRONMENT(voice_api.QWEN, runner=ran.append, root=tmp_path / "voice")
    venv, install = ran
    assert venv == ["/bin/uv", "venv", str(tmp_path / "voice" / ".venv"), "--python", "3.11"]
    assert install[:4] == ["/bin/uv", "pip", "install", "--python"]
    assert str(tmp_path / "voice") in install[4] and install[5:] == boot.PACKAGES      # never the main environment
    ran.clear()
    REAL_ENVIRONMENT(voice_api.KOKORO, runner=ran.append, root=tmp_path / "voice")
    assert ran[-1][-2:] == ["--no-deps", boot.READER] and len(ran) == 3
    monkeypatch.setattr(boot, "PACKAGES", [*boot.PACKAGES, "phonemizer==3.3.0"])
    with pytest.raises(SystemExit, match="A GPL package"):
        REAL_ENVIRONMENT(voice_api.QWEN, runner=ran.append, root=tmp_path / "voice")


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.id)
def test_weights_come_from_the_pinned_revision_and_are_checked(tmp_path, engine):
    """Pin each file and check it, whatever the repository serves later."""
    asked = []

    def fake_download(repo_id, filename, revision=None):
        asked.append((repo_id, filename, revision))
        path = tmp_path / Path(filename).name
        path.write_bytes(b"swapped")
        return str(path)

    files = boot.FILES[engine.id]
    with pytest.raises(SystemExit, match="not the file we tested"):
        REAL_FETCH(engine, fake_download)
    assert asked == [(engine.repo, files[0][0], engine.revision)]            # stopped at the first
    assert len(engine.revision) == 40 and all(sha is None or len(sha) == 64 for _, _, sha in files)
    # every file the runner insists on is one the installer fetches, and nothing more
    assert set(engine.needs) == {name for name, _, _ in files} - {"model.safetensors.index.json"}
    # and every file too big to eyeball carries a hash
    assert all(sha for _, size, sha in files if size > 10_000_000)


def test_a_file_that_matches_its_hash_passes(tmp_path, monkeypatch):
    body = b"the real thing"
    monkeypatch.setitem(boot.FILES, "qwen", [("model.safetensors", len(body), hashlib.sha256(body).hexdigest()),
                                             ("config.json", 2, None)])
    fetched = []

    def fake_download(repo_id, filename, revision=None):
        fetched.append(filename)
        path = tmp_path / Path(filename).name
        path.write_bytes(body)
        return str(path)

    REAL_FETCH(voice_api.QWEN, fake_download)
    assert fetched == ["model.safetensors", "config.json"]


def test_verify_reads_the_files_on_disk_and_fetches_nothing(tmp_path, monkeypatch, capsys):
    engine = voice_api.QWEN
    body = b"weights"
    monkeypatch.setitem(boot.FILES, "qwen", [("model.safetensors", len(body), hashlib.sha256(body).hexdigest()),
                                             ("config.json", 2, None)])
    assert boot.verify(engine, hub=tmp_path) == ["its model files (3.08 GB)"]
    snapshot = tmp_path / f"models--{engine.repo.replace('/', '--')}" / "snapshots" / engine.revision
    snapshot.mkdir(parents=True)
    (snapshot / "model.safetensors").write_bytes(body)
    assert boot.verify(engine, hub=tmp_path) == ["config.json is missing"]
    (snapshot / "config.json").write_text("{}")
    assert boot.verify(engine, hub=tmp_path) == []
    (snapshot / "model.safetensors").write_bytes(b"swapped")
    assert boot.verify(engine, hub=tmp_path) == ["model.safetensors is not the file we tested"]
    monkeypatch.setattr(boot, "verify", lambda engine: ["model.safetensors is not the file we tested"])
    assert boot.main(["--engine", "qwen", "--verify"]) == 1 and "not the file we tested" in capsys.readouterr().out
