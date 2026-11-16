"""Kits: a character and the gear made for it, kept as a list of asset ids in output/.kits.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "viewer"))
import kits_api as ka

KNIGHT, SWORDS, SHIELDS = "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"


def test_no_file_means_no_kits(tmp_path):
    assert ka.list_kits(tmp_path) == []


def test_a_broken_file_reads_as_no_kits_instead_of_failing(tmp_path):
    (tmp_path / ka.KITS_NAME).write_text("{not json", encoding="utf-8")
    assert ka.list_kits(tmp_path) == []
    (tmp_path / ka.KITS_NAME).write_text(json.dumps({"kits": [{"id": "kit-x"}, "junk"]}), encoding="utf-8")
    assert ka.list_kits(tmp_path) == []   # rows without a name and a character are skipped


def test_making_a_kit_names_it_after_the_character_and_saves_it(tmp_path):
    kit = ka.make_kit(tmp_path, KNIGHT, "", character_name="Rune knight", now=lambda: 100)
    assert kit["name"] == "Rune knight kit"
    assert kit["character"] == KNIGHT and kit["gear"] == [] and kit["created"] == 100
    assert ka.KIT_ID.fullmatch(kit["id"])
    assert ka.list_kits(tmp_path) == [kit]
    assert json.loads((tmp_path / ka.KITS_NAME).read_text())["kits"][0]["id"] == kit["id"]


def test_ids_are_checked_before_anything_is_written(tmp_path):
    with pytest.raises(ValueError, match="not an asset id"):
        ka.make_kit(tmp_path, "../etc", "x")
    with pytest.raises(ValueError, match="no such asset"):
        ka.make_kit(tmp_path, KNIGHT, "x", known={SWORDS})
    assert not (tmp_path / ka.KITS_NAME).exists()


def test_gear_is_added_once_and_removed(tmp_path):
    kit = ka.make_kit(tmp_path, KNIGHT, "Knight")
    ka.add_to_kit(tmp_path, kit["id"], SWORDS)
    ka.add_to_kit(tmp_path, kit["id"], SWORDS)   # twice is still once
    after = ka.add_to_kit(tmp_path, kit["id"], SHIELDS)
    assert after["gear"] == [SWORDS, SHIELDS]
    assert ka.remove_from_kit(tmp_path, kit["id"], SWORDS)["gear"] == [SHIELDS]


def test_the_character_is_not_also_gear(tmp_path):
    kit = ka.make_kit(tmp_path, KNIGHT, "Knight")
    with pytest.raises(ValueError, match="already the kit's character"):
        ka.add_to_kit(tmp_path, kit["id"], KNIGHT)
    with pytest.raises(ValueError, match="keeps its character"):
        ka.remove_from_kit(tmp_path, kit["id"], KNIGHT)


def test_picking_another_try_swaps_it_in_where_the_old_one_was(tmp_path):
    kit = ka.make_kit(tmp_path, KNIGHT, "Knight")
    ka.add_to_kit(tmp_path, kit["id"], SWORDS)
    ka.add_to_kit(tmp_path, kit["id"], SHIELDS)
    other = "dddddddddddd"
    assert ka.swap_in_kit(tmp_path, kit["id"], SWORDS, other)["gear"] == [other, SHIELDS]
    assert ka.swap_in_kit(tmp_path, kit["id"], KNIGHT, "eeeeeeeeeeee")["character"] == "eeeeeeeeeeee"
    with pytest.raises(ValueError, match="not in this kit"):
        ka.swap_in_kit(tmp_path, kit["id"], "ffffffffffff", other)
    # swapping onto one already in the kit merges, never lists it twice
    assert ka.swap_in_kit(tmp_path, kit["id"], SHIELDS, other)["gear"] == [other]


def test_rename_and_delete_touch_only_the_kit(tmp_path):
    kit = ka.make_kit(tmp_path, KNIGHT, "Knight")
    (tmp_path / "keep.glb").write_bytes(b"model")
    assert ka.rename_kit(tmp_path, kit["id"], "  Paladin   set ")["name"] == "Paladin set"
    assert ka.rename_kit(tmp_path, kit["id"], "")["name"] == "Paladin set"   # an empty name is ignored
    ka.delete_kit(tmp_path, kit["id"])
    assert ka.list_kits(tmp_path) == []
    assert (tmp_path / "keep.glb").read_bytes() == b"model"


def test_an_unknown_kit_says_so(tmp_path):
    with pytest.raises(ValueError, match="no such kit"):
        ka.add_to_kit(tmp_path, "kit-00000000", SWORDS)
    with pytest.raises(ValueError, match="not a kit id"):
        ka.delete_kit(tmp_path, "../../x")


def test_kits_for_an_asset(tmp_path):
    one = ka.make_kit(tmp_path, KNIGHT, "One")
    two = ka.make_kit(tmp_path, SWORDS, "Two")
    ka.add_to_kit(tmp_path, two["id"], SHIELDS)
    kits = ka.list_kits(tmp_path)
    assert [k["id"] for k in ka.kits_with(kits, KNIGHT)] == [one["id"]]
    assert [k["id"] for k in ka.kits_with(kits, SHIELDS)] == [two["id"]]


def test_the_kit_routes_end_to_end(tmp_path, monkeypatch):
    """The studio's calls, through the real server, on a Library with one character and one sheet."""
    import http.server
    import threading
    import urllib.error
    import urllib.request

    import generate_api as api

    monkeypatch.setattr(api, "OUTPUT_ROOT", tmp_path)
    library = [{"id": KNIGHT, "name": "Rune knight"}, {"id": SWORDS, "name": "Swords"}]
    monkeypatch.setattr(api, "list_assets", lambda output: library)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/api/kits"

    def call(url, body=None):
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json",
                                                  "X-AssetFurnace-Request": "studio"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    try:
        assert call(base) == (200, {"kits": []})
        status, kit = call(base, {"character": KNIGHT})
        assert status == 201 and kit["name"] == "Rune knight kit"
        assert call(base, {"character": "dddddddddddd"})[0] == 422            # not in the Library
        assert call(f"{base}/{kit['id']}/add", {"asset": SWORDS})[1]["gear"] == [SWORDS]
        assert call(f"{base}/{kit['id']}/add", {"asset": "dddddddddddd"})[0] == 422
        assert call(f"{base}/{kit['id']}/name", {"name": "Paladin"})[1]["name"] == "Paladin"
        assert call(f"{base}/{kit['id']}/remove", {"asset": SWORDS})[1]["gear"] == []
        library.append({"id": "eeeeeeeeeeee", "name": "Rune knight · v2"})
        assert call(f"{base}/{kit['id']}/swap", {"from": KNIGHT, "to": "eeeeeeeeeeee"})[1]["character"] == "eeeeeeeeeeee"
        assert call(f"{base}/{kit['id']}/delete", {})[1] == {"id": kit["id"], "deleted": True}
        assert call(base)[1] == {"kits": []}
        assert call(f"{base}/kit-00000000/add", {"asset": SWORDS})[0] == 422
    finally:
        server.shutdown()
        server.server_close()
