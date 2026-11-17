"""Repaint an existing 3D model from a picture with the Hunyuan3D paint stage (MLX).

The paint stage only needs a mesh and a picture, so any model can be given a new texture:
one from another engine, a finished one, or one you brought. This runs the same paint call
as scripts/hunyuan_mlx_xiong_generate.py (shape, then paint), with your mesh in place of the
shape stage's. The studio's canvas runs it for its Repaint box.

Usage:
    hunyuan_mlx/shape/.venv/bin/python scripts/hunyuan_mlx_retexture.py \
        model.glb picture.png output.glb \
        [--decimation-target 300000] [--paint-seed 0] [--paint-res 512] \
        [--paint-steps 15] [--paint-tex 4096]

The result carries the Hunyuan3D paint licence (the Tencent community licence, not licensed
in the EU, UK or South Korea) as well as the repainted model's own: its sidecar names both.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from hunyuan_mlx_xiong_generate import (
    run_paint,
    run_remesh,
)

from image_to_3dlab.provenance import (
    set_glb_generator,
    write_hunyuan_sidecar,
)

BACKEND = "hunyuan-paint"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", type=Path, help="the 3D model to repaint (GLB, OBJ...)")
    parser.add_argument("image", type=Path, help="the picture to paint it from")
    parser.add_argument("output", type=Path, help="where the repainted GLB goes")
    parser.add_argument("--decimation-target", type=int, default=300_000,
                        help="fewer faces first if the model has more (the texture bake stalls past ~500k)")
    parser.add_argument("--paint-seed", type=int, default=0)
    parser.add_argument("--paint-res", type=int, default=512)
    parser.add_argument("--paint-steps", type=int, default=15)
    parser.add_argument("--paint-tex", type=int, default=4096)
    return parser.parse_args(argv)


def load_mesh(path: Path):
    """The model's geometry as one mesh (its old textures are not needed: the paint replaces them)."""
    import trimesh

    loaded = trimesh.load(str(path), force="mesh", process=False)
    if not hasattr(loaded, "faces") or len(loaded.faces) == 0:
        raise SystemExit(f"{path} has no faces to paint")
    return trimesh.Trimesh(vertices=loaded.vertices, faces=loaded.faces, process=False)


def source_record(model: Path) -> dict | None:
    """The repainted model's own licence record, when it has one beside it."""
    sidecar = model.with_suffix(".provenance.json")
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def note_source(sidecar: Path, model: Path) -> None:
    """Add the repainted model to the output's record: its shape came from there."""
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    record["derived_from"] = {"path": str(model), "provenance": source_record(model)}
    sidecar.write_text(json.dumps(record, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    t0 = time.time()
    print(f"[{BACKEND}] model={args.model} image={args.image}", flush=True)
    mesh = load_mesh(args.model)
    print(f"model loaded: {len(mesh.vertices):,} verts, {len(mesh.faces):,} faces ({time.time() - t0:.0f}s)", flush=True)
    mesh, _ = run_remesh(mesh, args.decimation_target, t0)
    shape_file = args.output.parent / f"{args.output.stem}_shape.obj"
    shape_file.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(str(shape_file))
    del mesh
    run_paint(shape_file, args.image, args.output, args.paint_seed, args.paint_res, args.paint_steps,
              args.paint_tex, t0)
    shape_file.unlink(missing_ok=True)
    set_glb_generator(args.output)
    sidecar = write_hunyuan_sidecar(
        args.output, args.image, backend=BACKEND, paint=True,
        parameters={"source_model": str(args.model), "paint_seed": args.paint_seed, "paint_res": args.paint_res,
                    "paint_steps": args.paint_steps, "paint_tex": args.paint_tex,
                    "decimation_target": args.decimation_target})
    note_source(sidecar, args.model)
    print(f"done ({time.time() - t0:.0f}s) -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
