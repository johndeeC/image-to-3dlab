"""Tests for scripts/bootstrap_music.py: what it says before a byte moves, and consent.

The two installs (the engine's environment and its weights) are always replaced here, so no
test can reach uv, the network or the real vendor/ folder.
"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "viewer")]

import backend_catalog
import bootstrap_music as boot
import music_api

REAL_FETCH = boot.fetch_weights
REAL_ENVIRONMENT = boot.install_environment


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch):
    monkeypatch.setattr(boot, "install_environment", lambda: pytest.fail("reached the real package install"))
    monkeypatch.setattr(boot, "install_weights", lambda: pytest.fail("reached the real download"))
    monkeypatch.setattr(boot.host, "host_platform", lambda: boot.host.APPLE)


def test_the_announcement_names_engine_route_size_parts_and_licence():
    text = boot.announcement()
    assert "ACE-Step 1.5" in text and "text to music" in text and "MLX and PyTorch" in text
    assert "10.1 GB total" in text and "MIT" in text and "commercial use included" in text
    assert boot.PACKAGES_SIZE in text and "vendor/music" in text             # the packages are named too
    assert boot.ENGINE.repo in text and boot.ENGINE.revision[:12] in text and boot.PACKAGE_COMMIT[:12] in text
    # the four parts, each with its size and what it does
    for label, size in (("music model", "4.79 GB"), ("song planner", "3.76 GB"), ("text reader", "1.21 GB"),
                        ("sound decoder", "0.34 GB")):
        assert any(label in line and size in line for line in text.splitlines()), label
    # the card's claim about its training data, as a claim
    assert "Its makers say" in text and "We have not checked that" in text
    assert "no recording is ever handed to it" in text


def test_the_parts_add_up_to_the_download(monkeypatch):
    assert round(boot.total_gb(), 1) == 10.1
    for part in boot.ENGINE.parts:
        assert boot.part_bytes(part) == part.size, part.label
    in_parts = sum(part.size for part in boot.ENGINE.parts)
    assert boot.total_bytes() - in_parts == 1_968                            # the repository's own config.json


def test_the_catalogue_card_quotes_the_same_repository_and_size():
    card = backend_catalog.BY_ID["music"]
    assert [w.source for w in card.weights] == [boot.ENGINE.repo]
    assert sum(w.bytes_expected for w in card.weights) == boot.total_bytes()
    assert card.install == "scripts/bootstrap_music.py" and card.kind == "sound"
    assert card.license_name == "MIT" and card.runs_on == (backend_catalog.APPLE,)
    assert card.build_probes == (music_api.ACE_STEP.python,)
    assert music_api.ACE_STEP.setup_id == card.id
    assert "we have not checked that" in card.tradeoff
    for part in boot.ENGINE.parts:
        assert part.label in card.weights[0].note and f"{part.size / 1e9:.2f} GB" in card.weights[0].note
    kinds = {b["id"]: b["kind"] for b in backend_catalog.catalog_status()["backends"]}
    assert kinds["music"] == "sound"


def test_nothing_downloads_without_a_yes_and_nobody_to_ask(monkeypatch, capsys):
    monkeypatch.setattr(boot.sys, "stdin", io.StringIO(""))
    assert boot.main([]) == 1
    said = capsys.readouterr().out
    assert "Refusing to download" in said and "About to install" in said


def test_a_typed_no_or_an_empty_answer_downloads_nothing(monkeypatch, capsys):
    monkeypatch.setattr(boot.sys, "stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})(""))
    for answer in ("n", ""):
        monkeypatch.setattr("builtins.input", lambda prompt, answer=answer: answer)
        assert boot.main([]) == 1
        assert "Nothing downloaded" in capsys.readouterr().out


def test_a_yes_installs_the_environment_then_the_weights(monkeypatch):
    done = []
    monkeypatch.setattr(boot, "install_environment", lambda: done.append("environment"))
    monkeypatch.setattr(boot, "install_weights", lambda: done.append("weights"))
    assert boot.main(["--yes"]) == 0 and done == ["environment", "weights"]


def test_a_machine_that_is_not_a_mac_is_told_so_and_downloads_nothing(monkeypatch, capsys):
    monkeypatch.setattr(boot.host, "host_platform", lambda: boot.host.NVIDIA)
    assert boot.main(["--yes"]) == 1
    assert "Apple Silicon" in capsys.readouterr().out


def test_check_only_looks(monkeypatch, capsys):
    monkeypatch.setattr(boot.music_api, "missing", lambda engine: ["its own Python environment"])
    assert boot.main(["--check"]) == 1
    assert "not installed" in capsys.readouterr().out
    monkeypatch.setattr(boot.music_api, "missing", lambda engine: [])
    assert boot.main(["--check"]) == 0


def test_the_studio_setup_button_runs_it_with_yes():
    import download_api
    command = download_api.COMMANDS["music"]           # the Setup page's own dialog is the asking
    assert command[-1] == "--yes" and command[1].endswith("bootstrap_music.py")


def test_the_packages_are_named_before_the_yes_until_they_are_installed(monkeypatch, tmp_path):
    import dataclasses

    import download_api
    monkeypatch.setattr(boot, "ENGINE", dataclasses.replace(music_api.ACE_STEP, environment=tmp_path))
    (extra,) = download_api.also_fetches("music")
    assert extra["size"] == boot.PACKAGES_SIZE and extra["licence"] == "MIT"
    assert extra["from"] == boot.PACKAGE_REPO and "ace-step" in extra["what"]
    python = boot.ENGINE.python
    python.parent.mkdir(parents=True)
    python.write_text("")
    assert download_api.also_fetches("music") == []


def test_the_environment_is_its_own_and_everything_in_it_is_pinned(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(boot.host, "find_uv", lambda: "/bin/uv")
    REAL_ENVIRONMENT(runner=ran.append, root=tmp_path / "music")
    venv, packages, engine = ran
    assert venv == ["/bin/uv", "venv", str(tmp_path / "music" / ".venv"), "--python", "3.11"]
    assert packages[:4] == ["/bin/uv", "pip", "install", "--python"]
    assert str(tmp_path / "music") in packages[4]                   # never the main environment
    assert packages[5:] == boot.PACKAGES
    # the engine itself: from its makers' repository at one commit, and without its own long
    # list of needs (a web page, training tools, other download sites)
    assert engine[5:] == ["--no-deps", boot.PACKAGE]
    assert len(boot.PACKAGE_COMMIT) == 40 and boot.PACKAGE.endswith(boot.PACKAGE_COMMIT)
    assert boot.PACKAGE_COMMIT in music_api.ACE_STEP.package        # the record names the same commit
    assert all(spec.count("==") == 1 and "git+" not in spec for spec in boot.PACKAGES)
    names = {spec.split("==")[0] for spec in boot.PACKAGES}
    assert {"torch", "torchaudio", "mlx", "mlx-lm", "transformers", "diffusers", "numpy",
            "vector-quantize-pytorch", "einops", "soundfile", "loguru"} <= names
    assert not names & {"gradio", "lightning", "modelscope", "tensorboard", "peft"}


def test_weights_come_from_the_pinned_revision_and_are_checked(tmp_path):
    """Pin each file and check it, whatever the repository serves later."""
    asked = []

    def fake_download(repo_id, filename, revision=None):
        asked.append((repo_id, filename, revision))
        path = tmp_path / Path(filename).name
        path.write_bytes(b"swapped")
        return str(path)

    with pytest.raises(SystemExit, match="not the file we tested"):
        REAL_FETCH(fake_download)
    assert asked == [(boot.ENGINE.repo, boot.FILES[0][0], boot.ENGINE.revision)]    # stopped at the first
    assert len(boot.ENGINE.revision) == 40
    assert all(sha is None or len(sha) == 64 for _, _, sha in boot.FILES)
    # every file the runner insists on is one the installer fetches, and the other way round
    assert set(music_api.ACE_STEP.needs) == {name for name, _, _ in boot.FILES}
    # every file too big to eyeball carries a hash, and so does the one PyTorch data file
    assert all(sha for name, size, sha in boot.FILES if size > 10_000_000 or name.endswith(".pt"))


def test_a_file_that_matches_its_hash_passes(tmp_path, monkeypatch):
    body = b"the real thing"
    monkeypatch.setattr(boot, "FILES", [("vae/diffusion_pytorch_model.safetensors", len(body),
                                         hashlib.sha256(body).hexdigest()), ("vae/config.json", 2, None)])
    fetched = []

    def fake_download(repo_id, filename, revision=None):
        fetched.append(filename)
        path = tmp_path / Path(filename).name
        path.write_bytes(body)
        return str(path)

    REAL_FETCH(fake_download)
    assert fetched == ["vae/diffusion_pytorch_model.safetensors", "vae/config.json"]


def test_verify_reads_what_is_on_disk_and_says_what_is_wrong(tmp_path, monkeypatch):
    body = b"weights"
    monkeypatch.setattr(boot, "FILES", [("vae/diffusion_pytorch_model.safetensors", len(body),
                                         hashlib.sha256(body).hexdigest()), ("vae/config.json", 2, None)])
    hub = tmp_path / "hub"
    assert boot.verify(hub) == ["its model files (10.1 GB)"]
    snapshot = hub / "models--ACE-Step--Ace-Step1.5" / "snapshots" / boot.ENGINE.revision
    (snapshot / "vae").mkdir(parents=True)
    (snapshot / "vae" / "diffusion_pytorch_model.safetensors").write_bytes(b"other")
    assert boot.verify(hub) == ["vae/diffusion_pytorch_model.safetensors is not the file we tested",
                                "vae/config.json is missing"]
    (snapshot / "vae" / "diffusion_pytorch_model.safetensors").write_bytes(body)
    (snapshot / "vae" / "config.json").write_text("{}")
    assert boot.verify(hub) == []
