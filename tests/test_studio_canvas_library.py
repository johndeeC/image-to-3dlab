"""What the canvas offers from the Library (viewer/studio/canvas-library.js), run under Node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required to execute browser ES modules")


def _run(body: str):
    url = (REPO / "viewer" / "studio" / "canvas-library.js").as_uri()
    result = subprocess.run([NODE, "--input-type=module", "--eval", f"import * as m from {json.dumps(url)};\n{body}"],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


LIBRARY = [
    {"name": "a small brass lantern · v2", "kind": "prop", "picture": "images/l/l.png",
     "model": "lantern__sf3d__190711/lantern__sf3d__190711.glb", "finished": "finish/lantern__finish/lantern_20k.glb"},
    {"name": "a knight", "kind": "character", "picture": "images/k/k.png", "back": "images/k/k__back.png",
     "model": "k__pixal3d__1/k__pixal3d__1.glb", "finished": "finish/k/k_hero.glb", "rigged": "animate/k/k_hero_rigged.glb"},
    {"name": "gear", "kind": "prop set", "picture": "images/g/g.png", "model": "g__pixal3d__2/g.glb",
     "props": [{"name": "left_boot", "file": "props/g/finished/left_boot/left_boot_LOD0.glb"}]},
    {"name": "hidden thing", "kind": "prop", "hidden": True, "model": "h/h.glb"},
    {"name": "only a picture", "kind": "picture", "picture": "images/o/o.png"},
]


def test_every_model_an_asset_has_is_offered_and_named_by_what_it_is():
    out = _run(f"console.log(JSON.stringify(m.libraryChoices({json.dumps(LIBRARY)}, 'model')))")
    assert [(c["name"], c["what"]) for c in out] == [
        ("a small brass lantern · v2", "Finished · 20k"), ("a small brass lantern · v2", "3D model · Fastest, rough"),
        ("a knight", "Finished · hero face"), ("a knight", "Rigged"), ("a knight", "3D model · Best detail"),
        ("left boot", "Prop from gear"), ("gear", "3D model · Best detail"),
    ]
    assert out[0]["picture"] == "images/l/l.png" and out[5]["picture"] is None


def test_pictures_include_backs_and_a_search_keeps_matching_names():
    out = _run(f"console.log(JSON.stringify([m.libraryChoices({json.dumps(LIBRARY)}, 'picture').map((c) => c.what),"
               f" m.libraryChoices({json.dumps(LIBRARY)}, 'model', 'KNIGHT').length, m.engineWords('x__newengine__y'),"
               f" m.detailWords('a_40k_rigged.glb')]))")
    assert out == [["Picture", "Picture", "Back picture", "Picture", "Picture"], 3, "", "40k"]


def test_a_canvas_result_is_traced_back_to_the_library_assets_that_hold_it():
    assets = [{"id": "a1", "model": "m/a.glb", "finished": "f/a.glb"}, {"id": "a2", "props": [{"file": "props/x/sword_LOD0.glb"}]},
              {"id": "a3", "rigged": "r/c.glb", "clips": [{"file": "r/c_walk.glb"}]}, {"id": "a4", "picture": "images/p.png"}]
    out = _run(f"console.log(JSON.stringify(m.assetIdsFor({json.dumps(assets)}, ['f/a.glb', 'm/a.glb', 'props/x/sword_LOD0.glb', 'r/c_walk.glb', 'nowhere.glb'])))")
    assert out == ["a1", "a2", "a3"]
