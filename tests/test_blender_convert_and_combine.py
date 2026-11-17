"""Save as (scripts/blender_convert.py) and Put together (scripts/blender_combine_parts.py): the parts
that run without Blender."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import blender_combine_parts as combine
import blender_convert as convert


def test_save_as_reads_in_and_out_and_refuses_an_unknown_type():
    assert convert.parse_args(["blender", "--", "a.glb", "b.fbx"]) == (Path("a.glb"), Path("b.fbx"))
    with pytest.raises(SystemExit, match="must end in"):
        convert.parse_args(["--", "a.glb", "b.stl"])
    with pytest.raises(SystemExit, match="usage"):
        convert.parse_args(["--", "a.glb"])


def test_parts_are_laid_out_in_a_centred_row_with_a_gap():
    assert combine.row_offsets([1.0, 1.0], gap=0.5) == [-0.75, 0.75]
    assert combine.row_offsets([2.0, 1.0, 1.0], gap=0.0) == [-1.0, 0.5, 1.5]
    assert combine.row_offsets([]) == []


def test_put_together_takes_an_out_and_two_or_more_parts_named_by_their_files():
    out, ins = combine.parse_args(["--", "all.glb", "a.glb", "b.glb"])
    assert out == Path("all.glb") and ins == [Path("a.glb"), Path("b.glb")]
    with pytest.raises(SystemExit, match="usage"):
        combine.parse_args(["--", "all.glb", "a.glb"])
    assert combine.object_name(Path("left-hand__part__20261116__sf3d__x.glb")) == "left-hand"
    assert combine.object_name(Path("props/x/finished/sword/sword_LOD0.glb")) == "sword"
