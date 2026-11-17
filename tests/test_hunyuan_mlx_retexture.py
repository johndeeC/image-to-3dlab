"""Repainting a model (scripts/hunyuan_mlx_retexture.py): the parts that run without the paint models."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hunyuan_mlx_retexture as rt


def test_the_command_line_takes_a_model_a_picture_and_where_to_write():
    args = rt.parse_args(["m.glb", "p.png", "out.glb", "--paint-seed", "4"])
    assert (args.model, args.image, args.output) == (Path("m.glb"), Path("p.png"), Path("out.glb"))
    assert args.paint_seed == 4 and args.decimation_target == 300_000 and args.paint_tex == 4096


def test_a_model_is_read_as_one_mesh_and_an_empty_one_refused(tmp_path):
    import trimesh

    box = tmp_path / "box.glb"
    parts = [trimesh.creation.box(), trimesh.creation.icosphere(subdivisions=1)]
    trimesh.Scene(parts).export(box)
    mesh = rt.load_mesh(box)
    assert len(mesh.faces) == sum(len(p.faces) for p in parts)   # both parts, joined
    empty = tmp_path / "empty.obj"
    empty.write_text("v 0 0 0\n")
    with pytest.raises(SystemExit, match="no faces"):
        rt.load_mesh(empty)


def test_the_repainted_model_keeps_the_source_licence_and_the_paint_licence(tmp_path):
    model, output = tmp_path / "knight.glb", tmp_path / "out.glb"
    model.write_bytes(b"glb")
    output.write_bytes(b"painted")
    model.with_suffix(".json").write_text(json.dumps({"license": {"name": "MIT", "conditions": []},
                                                      "output": {"classification": "commercial-clear"}}))
    paint = tmp_path / "out.provenance.json"
    paint.write_text(json.dumps({"license": {"name": "Tencent Hunyuan Community", "conditions": ["not EU/UK/KR"]},
                                 "output": {"classification": "territory-restricted"}}))
    record = json.loads(rt.write_record(model, output, paint).read_text())
    assert record["operation"] == "repaint" and record["license"]["name"] == "MIT + Tencent Hunyuan Community"
    assert "not EU/UK/KR" in record["license"]["conditions"] and record["source_record"]["license"]["name"] == "MIT"
