"""Put several 3D models into one file, side by side, each its own object, headless.

Run (headless): blender --background --python-exit-code 1 --factory-startup --python scripts/blender_combine_parts.py -- OUT.glb IN1.glb IN2.glb [IN3.glb ...]

Each model keeps its own mesh and texture under an object named after its file, laid out in
a row on the ground with a gap between them, so they can be arranged into one character or
scene in Blender. Nothing is moved into place for you: the parts carry no record of where
they sat in the picture. The canvas's Put together box runs this.
"""

from __future__ import annotations

import sys
from pathlib import Path

GAP = 0.15   # metres between neighbours


def parse_args(argv: list[str]) -> tuple[Path, list[Path]]:
    """OUT and the INs from Blender's command line (everything after `--`)."""
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    if len(argv) < 3:
        raise SystemExit("usage: ... -- OUT.glb IN1.glb IN2.glb [...]")
    return Path(argv[0]), [Path(a) for a in argv[1:]]


def row_offsets(widths: list[float], gap: float = GAP) -> list[float]:
    """Where each part's centre goes along X: in a row, `gap` apart, the whole row centred on 0."""
    if not widths:
        return []
    total = sum(widths) + gap * (len(widths) - 1)
    out, left = [], -total / 2
    for width in widths:
        out.append(left + width / 2)
        left += width + gap
    return out


def object_name(path: Path) -> str:
    """A part's name from its file: `left-hand__part__..._LOD0.glb` -> `left-hand`."""
    return path.stem.split("__")[0].removesuffix("_LOD0") or path.stem


def main() -> int:
    import bpy
    from mathutils import Vector

    destination, sources = parse_args(list(sys.argv))
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    groups = []
    for source in sources:
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(source))
        added = [o for o in bpy.data.objects if o not in before]
        holder = bpy.data.objects.new(object_name(source), None)
        bpy.context.scene.collection.objects.link(holder)
        for obj in added:
            if obj.parent is None:
                obj.parent = holder
        bpy.context.view_layer.update()
        corners = [o.matrix_world @ Vector(c) for o in added if o.type == "MESH" for c in o.bound_box]
        low = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
        high = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
        groups.append((holder, low, high))
    for (holder, low, high), x in zip(groups, row_offsets([h.x - lo.x for _, lo, h in groups])):
        centre = (low + high) / 2
        holder.location = Vector((x - centre.x, -centre.y, -low.z))   # side by side, standing on the ground
    destination.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(destination), export_format="GLB")
    print(f"COMBINE:: {len(sources)} parts -> {destination.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
