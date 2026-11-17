"""Tool jobs the canvas runs (viewer/tool_jobs.py): cut out, remesh, unwrap, save as, put together.
Blender itself is not run here; a stand-in command takes its place."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import tool_jobs as tj


def _picture(path: Path) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (200, 30, 30)).save(path)
    return path


def test_a_tool_reads_only_files_inside_output_of_the_right_kind(tmp_path):
    _picture(tmp_path / "images" / "a" / "a.png")
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "m.glb").write_bytes(b"glb")
    assert tj.inside(tmp_path, "images/a/a.png", tj.PICTURES).name == "a.png"
    for relative, kinds in (("m/m.glb", tj.PICTURES), ("images/a/a.png", tj.MODELS), ("../x.png", tj.PICTURES), ("", tj.MODELS)):
        with pytest.raises(ValueError, match="in output/"):
            tj.inside(tmp_path, relative, kinds)


def test_what_a_tool_makes_lands_in_a_folder_of_its_own_named_by_its_source(tmp_path):
    first = tj.made_path(tmp_path, "tools", "knight", "remesh", ".glb", "20261116-220000")
    assert first == tmp_path / "tools" / "knight__remesh__20261116-220000" / "knight__remesh__20261116-220000.glb"
    first.parent.mkdir(parents=True)
    assert tj.made_path(tmp_path, "tools", "knight", "remesh", ".glb", "20261116-220000").parent.name.endswith("-2")
    assert tj.stem_of(Path("helmet__part__2026/helmet__part__2026__sf3d__x.glb")) == "helmet"


def test_a_cut_out_writes_a_clear_backed_picture_and_keeps_the_licence_record(tmp_path):
    from PIL import Image

    source = _picture(tmp_path / "images" / "k" / "k.png")
    source.with_suffix(".provenance.json").write_text(json.dumps({"license": {"name": "Apache-2.0"}}))
    clear = lambda image: (Image.new("RGBA", image.size, (0, 0, 0, 0)), "stub")
    jobs = tj.ToolJobs()
    job = jobs.start("cutout", tj.cutout_work(tmp_path, "images/k/k.png", cut=clear), background=False)
    assert job.status == "done" and job.payload()["picture"].startswith("images/k__cutout__")
    made = tmp_path / job.result["picture"]
    with Image.open(made) as image:
        assert image.mode == "RGBA"
    record = json.loads(made.with_suffix(".provenance.json").read_text())
    assert record["operation"] == "cutout" and record["license"]["name"] == "Apache-2.0"


def test_a_blender_tool_runs_its_script_on_the_model_and_records_what_it_made(tmp_path, monkeypatch):
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "m.glb").write_bytes(b"glb")
    seen = []

    def fake_run(job, command):
        seen.append(command)
        Path(command[command.index("--") + 2]).write_bytes(b"remeshed")   # OUT follows IN
    monkeypatch.setattr(tj, "run_command", fake_run)
    job = tj.ToolJobs().start("remesh", tj.remesh_work(tmp_path, "m/m.glb", 8000, 1024, Path("/bin/blender")), background=False)
    assert job.status == "done", job.error
    assert job.result["model"].startswith("tools/m__remesh__") and job.result["model"].endswith(".glb")
    command = seen[0]
    assert command[0] == "/bin/blender" and command[command.index("--python") + 1].endswith("blender_retopo_bake.py")
    assert command[-2:] == ["8000", "1024"]
    assert json.loads((tmp_path / job.result["model"]).with_suffix(".provenance.json").read_text())["operation"] == "remesh"


def test_put_together_passes_out_first_and_save_as_lands_in_exports(tmp_path, monkeypatch):
    for n in ("a", "b"):
        (tmp_path / n).mkdir()
        (tmp_path / n / f"{n}.glb").write_bytes(b"glb")
    seen = []

    def fake_run(job, command):
        seen.append(command)
        after = command[command.index("--") + 1:]
        out = after[0] if job.kind == "combine" else after[1]
        Path(out).write_bytes(b"x")
    monkeypatch.setattr(tj, "run_command", fake_run)
    jobs = tj.ToolJobs()
    together = jobs.start("combine", tj.combine_work(tmp_path, ["a/a.glb", "b/b.glb"], Path("blender")), background=False)
    saved = jobs.start("export", tj.export_work(tmp_path, "a/a.glb", "FBX", Path("blender")), background=False)
    assert together.result["model"].startswith("tools/a__combine__")
    assert seen[0][seen[0].index("--") + 2:] == [str((tmp_path / "a" / "a.glb").resolve()), str((tmp_path / "b" / "b.glb").resolve())]
    assert saved.result["file"].startswith("exports/a__export__") and saved.result["file"].endswith(".fbx")
    record = json.loads((tmp_path / together.result["model"]).with_suffix(".provenance.json").read_text())
    assert record["operation"] == "combine" and record["input"]["path"] == str((tmp_path / "a" / "a.glb").resolve())
    assert record["component_records"] == [{"source": str((tmp_path / "b" / "b.glb").resolve())}]   # no record of its own: said so


def test_tools_refuse_what_they_cannot_do_before_starting(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "a.glb").write_bytes(b"glb")
    with pytest.raises(ValueError, match="needs Blender"):
        tj.remesh_work(tmp_path, "a/a.glb", 8000, 1024, None)
    with pytest.raises(ValueError, match="faces is 500"):
        tj.remesh_work(tmp_path, "a/a.glb", 5, 1024, Path("blender"))
    with pytest.raises(ValueError, match="save as one of"):
        tj.export_work(tmp_path, "a/a.glb", "stl", Path("blender"))
    with pytest.raises(ValueError, match="put together 2"):
        tj.combine_work(tmp_path, ["a/a.glb"], Path("blender"))


def test_one_tool_job_at_a_time_a_failure_ends_it_and_cancel_stops_it(tmp_path):
    import threading

    jobs = tj.ToolJobs()
    gate = threading.Event()
    slow = jobs.start("remesh", lambda job: (gate.wait(5), {"model": "x"})[1])
    with pytest.raises(RuntimeError, match="another tool job"):
        jobs.start("unwrap", lambda job: {})
    assert jobs.cancel(slow.id) is True
    gate.set()
    for _ in range(100):
        if slow.status != "running":
            break
        threading.Event().wait(0.01)
    assert slow.status == "cancelled"
    broken = jobs.start("unwrap", lambda job: (_ for _ in ()).throw(RuntimeError("Blender fell over")), background=False)
    assert broken.status == "error" and broken.payload()["error"] == "Blender fell over"
    assert jobs.cancel(broken.id) is False


def test_a_command_writes_its_output_to_the_log_and_a_failure_raises(tmp_path):
    job = tj.ToolJob("unwrap")
    tj.run_command(job, [sys.executable, "-c", "print('BAKE:: 50%')"])
    assert list(job.log) == ["BAKE:: 50%"]
    with pytest.raises(RuntimeError, match="exit code 3"):
        tj.run_command(job, [sys.executable, "-c", "raise SystemExit(3)"])


def test_a_right_view_is_the_left_one_mirrored_beside_it(tmp_path):
    from PIL import Image

    left = tmp_path / "images" / "k" / "k__left.png"
    left.parent.mkdir(parents=True)
    picture = Image.new("RGB", (4, 2), (0, 0, 0))
    picture.putpixel((0, 0), (255, 0, 0))
    picture.save(left)
    job = tj.ToolJobs().start("mirror", tj.mirror_work(tmp_path, "images/k/k__left.png"), background=False)
    assert job.status == "done" and job.result["picture"] == "images/k/k__right.png"
    with Image.open(tmp_path / job.result["picture"]) as right:
        assert right.getpixel((3, 0)) == (255, 0, 0)          # what was on the left is on the right
    assert tj.mirrored_name(Path("a/b.png")).name == "b__mirrored.png"
