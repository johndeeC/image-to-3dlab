"""Kits: one character and the gear (prop sets, single props) made for it.

A kit is only a list of asset ids in `output/.kits.json`, beside the Library's own
`.assets-meta.json`: no file is moved or renamed, and deleting a kit removes the grouping
only. An id whose asset was deleted stays listed; the studio shows it as missing.
See docs/kits.md.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

KITS_NAME = ".kits.json"
KIT_ID = re.compile(r"^kit-[0-9a-f]{8}$")
ASSET_ID = re.compile(r"^[0-9a-f]{12}$")
NAME_LIMIT = 80
_LOCK = threading.Lock()   # the server answers requests on threads; one write at a time


def _clean_name(name: str) -> str:
    return " ".join(str(name or "").split())[:NAME_LIMIT]


def _read(output: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((output / KITS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("kits") if isinstance(data, dict) else None
    kits = []
    for row in rows if isinstance(rows, list) else []:
        if not (isinstance(row, dict) and KIT_ID.fullmatch(str(row.get("id", "")))
                and isinstance(row.get("name"), str) and ASSET_ID.fullmatch(str(row.get("character", "")))):
            continue
        gear = [g for g in row.get("gear", []) if isinstance(g, str) and ASSET_ID.fullmatch(g)]
        kits.append({"id": row["id"], "name": row["name"], "character": row["character"],
                     "gear": list(dict.fromkeys(gear)), "created": row.get("created", 0)})
    return kits


def _write(output: Path, kits: list[dict[str, Any]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    partial = output / (KITS_NAME + ".part")
    partial.write_text(json.dumps({"kits": kits}, indent=1), encoding="utf-8")
    partial.replace(output / KITS_NAME)   # never half a file


def _check_asset(asset_id: str, known: Iterable[str] | None) -> None:
    if not ASSET_ID.fullmatch(asset_id or ""):
        raise ValueError(f"not an asset id: {asset_id!r}")
    if known is not None and asset_id not in set(known):
        raise ValueError(f"no such asset: {asset_id}")


def _change(output: Path, kit_id: str, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    if not KIT_ID.fullmatch(kit_id or ""):
        raise ValueError(f"not a kit id: {kit_id!r}")
    with _LOCK:
        kits = _read(output)
        kit = next((k for k in kits if k["id"] == kit_id), None)
        if kit is None:
            raise ValueError(f"no such kit: {kit_id}")
        change(kit)
        _write(output, kits)
        return kit


def list_kits(output: Path) -> list[dict[str, Any]]:
    """Every kit, oldest first."""
    return _read(output)


def kits_with(kits: list[dict[str, Any]], asset_id: str) -> list[dict[str, Any]]:
    """The kits an asset is in, as the character or as gear."""
    return [k for k in kits if k["character"] == asset_id or asset_id in k["gear"]]


def make_kit(output: Path, character: str, name: str = "", *, character_name: str = "",
             known: Iterable[str] | None = None, now: Callable[[], float] = time.time) -> dict[str, Any]:
    """A new kit for one character. `known` is the Library's ids, when the caller has them."""
    _check_asset(character, known)
    clean = _clean_name(name) or _clean_name(f"{character_name or 'New'} kit")
    with _LOCK:
        kits = _read(output)
        taken = {k["id"] for k in kits}
        kit_id = f"kit-{os.urandom(4).hex()}"
        while kit_id in taken:
            kit_id = f"kit-{os.urandom(4).hex()}"
        kit = {"id": kit_id, "name": clean, "character": character, "gear": [], "created": int(now())}
        kits.append(kit)
        _write(output, kits)
    return kit


def add_to_kit(output: Path, kit_id: str, asset_id: str, *, known: Iterable[str] | None = None) -> dict[str, Any]:
    """File a prop set or prop under the kit. Adding one twice keeps it once."""
    _check_asset(asset_id, known)

    def add(kit: dict[str, Any]) -> None:
        if asset_id == kit["character"]:
            raise ValueError("That is already the kit's character.")
        if asset_id not in kit["gear"]:
            kit["gear"].append(asset_id)
    return _change(output, kit_id, add)


def remove_from_kit(output: Path, kit_id: str, asset_id: str) -> dict[str, Any]:
    """Take gear out of the kit. The asset itself stays in the Library."""
    _check_asset(asset_id, None)

    def remove(kit: dict[str, Any]) -> None:
        if asset_id == kit["character"]:
            raise ValueError("A kit keeps its character; delete the kit instead.")
        kit["gear"] = [g for g in kit["gear"] if g != asset_id]
    return _change(output, kit_id, remove)


def swap_in_kit(output: Path, kit_id: str, old: str, new: str, *, known: Iterable[str] | None = None) -> dict[str, Any]:
    """Use another try (version) in place of one: the kit's character or one of its gear."""
    _check_asset(old, None)
    _check_asset(new, known)

    def swap(kit: dict[str, Any]) -> None:
        if old == kit["character"]:
            kit["character"] = new
            kit["gear"] = [g for g in kit["gear"] if g != new]
        elif old in kit["gear"]:
            kit["gear"] = list(dict.fromkeys(new if g == old else g for g in kit["gear"]))
        else:
            raise ValueError(f"{old} is not in this kit")
    return _change(output, kit_id, swap)


def rename_kit(output: Path, kit_id: str, name: str) -> dict[str, Any]:
    """A new name for the kit; an empty one keeps the old name."""
    clean = _clean_name(name)

    def rename(kit: dict[str, Any]) -> None:
        if clean:
            kit["name"] = clean
    return _change(output, kit_id, rename)


def delete_kit(output: Path, kit_id: str) -> None:
    """Remove the grouping. Every asset in it stays in the Library."""
    if not KIT_ID.fullmatch(kit_id or ""):
        raise ValueError(f"not a kit id: {kit_id!r}")
    with _LOCK:
        kits = _read(output)
        if not any(k["id"] == kit_id for k in kits):
            raise ValueError(f"no such kit: {kit_id}")
        _write(output, [k for k in kits if k["id"] != kit_id])
