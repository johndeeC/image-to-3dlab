"""Tests for scripts/bootstrap_sound_effects.py: what it says before a byte moves, and consent.

The two installs (the engine's environment and its weights) are always replaced here, so no
test can reach uv, the network or the real vendor/ folder.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "viewer")]

import backend_catalog
import bootstrap_sound_effects as boot
import sound_api

REAL_FETCH = boot.fetch_weights
REAL_ENVIRONMENT = boot.install_environment


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch):
    monkeypatch.setattr(boot, "install_environment", lambda: pytest.fail("reached the real package install"))
    monkeypatch.setattr(boot, "install_weights", lambda: pytest.fail("reached the real download"))
    monkeypatch.setattr(boot.host, "host_platform", lambda: boot.host.APPLE)


def test_the_announcement_names_engine_route_size_and_licence():
    text = boot.announcement()
    assert "MOSS-SoundEffect v2.0" in text and "4-bit MLX" in text and "moss-sfx-mlx" in text
    assert "6.4 GB" in text and "Apache-2.0" in text and "commercial" in text
    assert boot.PACKAGES_SIZE in text and "vendor/moss-sfx" in text          # the packages are named too
    assert boot.ENGINE.repo in text and boot.ENGINE.revision[:12] in text and boot.PACKAGE_COMMIT[:12] in text
    assert "training data is not published" in text
    for name, size, checked in boot.FILES:
        if checked:
            assert name in text and f"{size / 1e9:.2f}" in text


def test_the_catalogue_card_quotes_the_same_repository_and_size():
    card = backend_catalog.BY_ID["sound-effects"]
    assert [w.source for w in card.weights] == [boot.ENGINE.repo]
    assert sum(w.bytes_expected for w in card.weights) == boot.total_bytes()
    assert card.install == "scripts/bootstrap_sound_effects.py" and card.kind == "sound"
    assert card.license_name == "Apache-2.0" and card.runs_on == (backend_catalog.APPLE,)
    # the card calls it installed only when the engine's own environment is there
    assert card.build_probes == (sound_api.MOSS.python,)
    assert sound_api.MOSS.setup_id == card.id


def test_nothing_downloads_without_a_yes_and_nobody_to_ask(monkeypatch, capsys):
    monkeypatch.setattr(boot.sys, "stdin", io.StringIO(""))
    assert boot.main([]) == 1
    said = capsys.readouterr().out
    assert "Refusing to download" in said and "About to install" in said


def test_a_typed_no_downloads_nothing(monkeypatch, capsys):
    monkeypatch.setattr(boot.sys, "stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})(""))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert boot.main([]) == 1
    assert "Nothing downloaded" in capsys.readouterr().out


def test_an_empty_answer_is_a_no(monkeypatch):
    monkeypatch.setattr(boot.sys, "stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})(""))
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert boot.main([]) == 1


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
    monkeypatch.setattr(boot.sound_api, "missing", lambda engine: ["its own Python environment"])
    assert boot.main(["--check"]) == 1
    assert "not installed" in capsys.readouterr().out
    monkeypatch.setattr(boot.sound_api, "missing", lambda engine: [])
    assert boot.main(["--check"]) == 0


def test_the_studio_setup_button_runs_it_with_yes():
    import download_api
    command = download_api.COMMANDS["sound-effects"]   # the Setup page's own dialog is the asking
    assert command[-1] == "--yes" and command[1].endswith("bootstrap_sound_effects.py")


def test_the_packages_are_named_before_the_yes_until_they_are_installed(monkeypatch, tmp_path):
    import dataclasses

    import download_api
    monkeypatch.setattr(boot, "ENGINE", dataclasses.replace(sound_api.MOSS, environment=tmp_path))
    (extra,) = download_api.also_fetches("sound-effects")
    assert extra["size"] == boot.PACKAGES_SIZE and extra["licence"] == "Apache-2.0"
    assert extra["from"] == boot.PACKAGE_REPO and "moss-sfx-mlx" in extra["what"]
    python = boot.ENGINE.python
    python.parent.mkdir(parents=True)
    python.write_text("")
    assert download_api.also_fetches("sound-effects") == []


def test_the_environment_is_its_own_and_the_package_is_pinned_to_a_commit(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(boot.host, "find_uv", lambda: "/bin/uv")
    REAL_ENVIRONMENT(runner=ran.append, root=tmp_path / "moss-sfx")
    venv, install = ran
    assert venv == ["/bin/uv", "venv", str(tmp_path / "moss-sfx" / ".venv"), "--python", "3.11"]
    assert install[:4] == ["/bin/uv", "pip", "install", "--python"]
    assert str(tmp_path / "moss-sfx") in install[4]                 # never the main environment
    assert install[5] == boot.PACKAGE and len(boot.PACKAGE_COMMIT) == 40 and boot.PACKAGE.endswith(boot.PACKAGE_COMMIT)
    assert install[6:] == boot.EXTRA_PACKAGES


def test_the_packages_the_port_imports_but_does_not_declare_are_installed_and_pinned():
    # moss-sfx-mlx at this commit imports mlx_lm (its text encoder), ftfy, regex and tqdm without
    # listing them; the first real run died on `No module named 'mlx_lm'`.
    names = {spec.split("==")[0] for spec in boot.EXTRA_PACKAGES}
    assert {"mlx-lm", "ftfy", "regex", "tqdm"} <= names
    assert all("==" in spec for spec in boot.EXTRA_PACKAGES)


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
    # every file the runner insists on is one the installer fetches
    assert set(sound_api.MOSS.needs) <= {name for name, _, _ in boot.FILES}
    # and every file too big to eyeball carries a hash
    assert all(sha for _, size, sha in boot.FILES if size > 10_000_000)


def test_a_file_that_matches_its_hash_passes(tmp_path, monkeypatch):
    import hashlib
    body = b"the real thing"
    monkeypatch.setattr(boot, "FILES", [("mlx/dit.safetensors", len(body), hashlib.sha256(body).hexdigest()),
                                        ("model_index.json", 2, None)])
    fetched = []

    def fake_download(repo_id, filename, revision=None):
        fetched.append(filename)
        path = tmp_path / Path(filename).name
        path.write_bytes(body)
        return str(path)

    REAL_FETCH(fake_download)
    assert fetched == ["mlx/dit.safetensors", "model_index.json"]
