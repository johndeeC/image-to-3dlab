"""Save a 3D model in another file type (FBX, OBJ or USDZ), headless, textures inside.

Run (headless): blender --background --python-exit-code 1 --factory-startup --python scripts/blender_convert.py -- IN.glb OUT.fbx

The type follows OUT's extension. FBX keeps a rig and its moves, with the textures packed
into the file; OBJ is shape and texture only (an .mtl and the pictures beside it); USDZ is
for Apple's Quick Look and Reality Composer. The canvas's Save as box runs this.
"""

from __future__ import annotations

import sys
from pathlib import Path

FORMATS = {".fbx": "FBX", ".obj": "OBJ", ".usdz": "USDZ"}


def parse_args(argv: list[str]) -> tuple[Path, Path]:
    """IN and OUT from Blender's command line (everything after `--`)."""
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    if len(argv) != 2:
        raise SystemExit("usage: ... -- IN.glb OUT.(fbx|obj|usdz)")
    source, destination = Path(argv[0]), Path(argv[1])
    if destination.suffix.lower() not in FORMATS:
        raise SystemExit(f"OUT must end in one of {', '.join(FORMATS)}")
    return source, destination


def main() -> int:
    import bpy

    source, destination = parse_args(list(sys.argv))
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    kind = destination.suffix.lower()
    if kind == ".fbx":
        bpy.ops.export_scene.fbx(filepath=str(destination), path_mode="COPY", embed_textures=True,
                                 add_leaf_bones=False, bake_anim=True)
    elif kind == ".obj":
        bpy.ops.wm.obj_export(filepath=str(destination), path_mode="COPY", export_materials=True)
    else:
        bpy.ops.wm.usd_export(filepath=str(destination), export_textures=True)
    if not destination.is_file():
        raise SystemExit(f"Blender did not write {destination}")
    print(f"CONVERT:: {source.name} -> {destination.name} ({FORMATS[kind]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
