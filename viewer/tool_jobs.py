"""Short tool jobs the canvas runs on files already in output/: cut out a picture's background,
quad-remesh a model, re-unwrap it, save it as FBX/OBJ/USDZ, put parts together in one file.

None of them runs a generative model. They are the background remover (already installed with
every 3D engine) and headless Blender scripts the repo already has, wrapped as jobs the chain
runner can start, follow and cancel the way it does every other step (`/api/tool/...`).
Every file made carries its source's licence record, with what was done to it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from image_to_3dlab import processes

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
TERMINAL = {"done", "error", "cancelled"}
PICTURES = {".png", ".jpg", ".jpeg", ".webp"}
MODELS = {".glb", ".gltf"}
FORMATS = (".fbx", ".obj", ".usdz")
MAX_PARTS = 24

# What each tool is called on screen and in its folder name.
LABELS = {"cutout": "Cut out background", "remesh": "Quad remesh", "unwrap": "UV unwrap",
          "export": "Save as", "combine": "Put together", "mirror": "Mirror"}


class ToolJob:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.status = "running"
        self.events: list[dict[str, Any]] = []
        self.log: deque[str] = deque(maxlen=200)
        self.result: dict[str, Any] = {}
        self.error = ""
        self.process: subprocess.Popen | None = None
        self.cancel_requested = False
        self.started = time.monotonic()

    def emit(self, message: str, percent: float | None = None, **extra: Any) -> None:
        event = {"phase": self.kind, "message": message, **extra}
        if percent is not None:
            event["overall_pct"] = percent
        self.events.append(event)

    def payload(self) -> dict[str, Any]:
        """The status the chain runner polls: {status, last_event, log_tail, error, and the file made}."""
        return {"status": self.status, "last_event": self.events[-1] if self.events else None,
                "log_tail": "\n".join(self.log), "error": self.error or None, **self.result}


class ToolJobs:
    """One tool job at a time (Blender and the remover both want the machine to themselves)."""

    def __init__(self):
        self.jobs: dict[str, ToolJob] = {}
        self.lock = threading.Lock()

    def running(self) -> ToolJob | None:
        """The slow tool job going, if any. A mirror (a picture flipped, in a blink) never counts:
        a four-view build's right side must not wait behind a side lane's remesh."""
        return next((j for j in self.jobs.values() if j.status not in TERMINAL and j.kind != "mirror"), None)

    def start(self, kind: str, work: Callable[[ToolJob], dict[str, Any]], *, background: bool = True) -> ToolJob:
        with self.lock:
            if kind != "mirror" and self.running() is not None:
                raise RuntimeError("another tool job is running; wait for it to finish")
            job = ToolJob(kind)
            self.jobs[job.id] = job
        job.emit(f"{LABELS.get(kind, kind)}: starting", 0)
        runner = lambda: _run(job, work)
        if background:
            threading.Thread(target=runner, daemon=True, name=f"tool-{kind}").start()
        else:
            runner()
        return job

    def get(self, job_id: str) -> ToolJob | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.status in TERMINAL:
            return False
        job.cancel_requested = True
        if job.process is not None and job.process.poll() is None:
            processes.terminate_group(job.process.pid)
        return True


def _run(job: ToolJob, work: Callable[[ToolJob], dict[str, Any]]) -> None:
    try:
        job.result = work(job)
        job.status = "cancelled" if job.cancel_requested else "done"
        job.emit("Cancelled." if job.cancel_requested else f"{LABELS.get(job.kind, job.kind)}: done", 100)
    except Exception as exc:   # a tool job must end, never hang as "running"
        job.status = "cancelled" if job.cancel_requested else "error"
        job.error = str(exc)
        job.emit(job.error)


def run_command(job: ToolJob, command: list[str]) -> None:
    """Run one subprocess, its output into the job's log; raise when it fails or is cancelled."""
    if job.cancel_requested:
        raise RuntimeError("cancelled")
    job.process = subprocess.Popen(command, cwd=str(REPO), env=processes.child_env(), stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, bufsize=1, **processes.TEXT_PIPE,
                                   **processes.group_popen_kwargs())
    assert job.process.stdout is not None
    for raw in job.process.stdout:
        line = raw.rstrip("\n")
        if line:
            job.log.append(line)
    code = job.process.wait()
    if job.cancel_requested:
        raise RuntimeError("cancelled")
    if code != 0:
        raise RuntimeError(f"{LABELS.get(job.kind, job.kind)} stopped (exit code {code}). The log has the details.")


# ---------------------------------------------------------------- files in and out
def inside(output: Path, relative: str, kinds: set[str] | tuple[str, ...]) -> Path:
    """A file the job may read: inside output/, existing, of the right kind."""
    root = output.resolve()
    path = (root / str(relative or "")).resolve()
    if root not in path.parents or not path.is_file() or path.suffix.lower() not in kinds:
        raise ValueError(f"not a {'picture' if kinds is PICTURES else '3D model'} in output/: {relative}")
    return path


def stem_of(path: Path) -> str:
    """A short readable name for what a tool made from `path`."""
    return re.sub(r"[^a-z0-9-]+", "-", path.stem.split("__")[0].lower()).strip("-")[:40] or "model"


def made_path(output: Path, folder: str, stem: str, kind: str, suffix: str, stamp: str | None = None) -> Path:
    """Where a tool's file lands: `<folder>/<stem>__<kind>__<time>/<same>.<suffix>`, its own folder
    (so the Library finds a model or picture as it finds any other)."""
    name = f"{stem}__{kind}__{stamp or time.strftime('%Y%m%d-%H%M%S')}"
    directory, n = output / folder / name, 2
    while directory.exists():
        directory = output / folder / f"{name}-{n}"
        n += 1
    return directory / f"{directory.name}{suffix}"


def carry_provenance(source: Path | list[Path], made: Path, kind: str) -> Path:
    """The made file's licence record (provenance.write_derived_sidecar): its source's licences and
    hashes, kept whole, and what was done to it. Put together keeps every part's record."""
    from image_to_3dlab.provenance import read_model_record, write_derived_sidecar

    sources = source if isinstance(source, list) else [source]
    others = [read_model_record(s) or {"source": str(s)} for s in sources[1:]]
    return write_derived_sidecar(sources[0], made, operation=kind, component_records=others)


def rel(output: Path, path: Path) -> str:
    return path.resolve().relative_to(output.resolve()).as_posix()


# ---------------------------------------------------------------- the tools
def cutout_work(output: Path, picture: str, cut: Callable | None = None) -> Callable[[ToolJob], dict[str, Any]]:
    """Cut a picture's subject out onto a clear background (the background remover)."""
    source = inside(output, picture, PICTURES)

    def work(job: ToolJob) -> dict[str, Any]:
        from PIL import Image

        cutter = cut
        if cutter is None:
            from image_to_3dlab import matte
            cutter = matte.cut_out
        job.emit("Cutting out the background", 30)
        with Image.open(source) as opened:
            if max(opened.size) > 8192:
                raise ValueError("that picture is too large to cut out")
            image, _model = cutter(opened.convert("RGB"))
        target = made_path(output, "images", stem_of(source), "cutout", ".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, "PNG")
        carry_provenance(source, target, "cutout")
        return {"picture": rel(output, target)}
    return work


def blender_work(output: Path, kind: str, script: str, sources: list[str], suffix: str, extra: list[str],
                 blender: Path | None, *, folder: str = "tools", out_first: bool = False) -> Callable[[ToolJob], dict[str, Any]]:
    """One headless Blender script on models in output/. `out_first`: the script takes OUT before the INs."""
    if blender is None:
        raise ValueError("This needs Blender. Setup installs it.")
    paths = [inside(output, s, MODELS) for s in sources]
    script_path = SCRIPTS / script

    def work(job: ToolJob) -> dict[str, Any]:
        target = made_path(output, folder, stem_of(paths[0]), kind, suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        ins = [str(p) for p in paths]
        args = [str(target), *ins] if out_first else [*ins, str(target)]
        job.emit(f"{LABELS[kind]} in Blender", 20)
        try:
            run_command(job, [str(blender), "--background", "--factory-startup", "--python-exit-code", "1",
                              "--python", str(script_path), "--", *args, *extra])
        except Exception:
            shutil.rmtree(target.parent, ignore_errors=True)
            raise
        if not target.is_file():
            raise RuntimeError(f"Blender did not write {target.name}")
        carry_provenance(paths if len(paths) > 1 else paths[0], target, kind)
        return {"model" if suffix == ".glb" else "file": rel(output, target)}
    return work


def remesh_work(output: Path, model: str, faces: int, size: int, blender: Path | None):
    if not 500 <= faces <= 200_000 or size not in (512, 1024, 2048, 4096):
        raise ValueError("faces is 500 to 200,000 and texture size is 512, 1024, 2048 or 4096")
    return blender_work(output, "remesh", "blender_retopo_bake.py", [model], ".glb", [str(faces), str(size)], blender)


def unwrap_work(output: Path, model: str, size: int, blender: Path | None):
    if size not in (512, 1024, 2048, 4096):
        raise ValueError("texture size is 512, 1024, 2048 or 4096")
    return blender_work(output, "unwrap", "blender_reunwrap_bake.py", [model], ".glb", [str(size)], blender)


def export_work(output: Path, model: str, fmt: str, blender: Path | None):
    suffix = f".{str(fmt).lower().lstrip('.')}"
    if suffix not in FORMATS:
        raise ValueError(f"save as one of {', '.join(f[1:].upper() for f in FORMATS)}")
    return blender_work(output, "export", "blender_convert.py", [model], suffix, [], blender, folder="exports")


def combine_work(output: Path, models: list[str], blender: Path | None):
    if not isinstance(models, list) or not 2 <= len(models) <= MAX_PARTS:
        raise ValueError(f"put together 2 to {MAX_PARTS} models")
    return blender_work(output, "combine", "blender_combine_parts.py", models, ".glb", [], blender, out_first=True)


def mirrored_name(picture: Path) -> Path:
    """Where a mirrored view goes: a left view `<front>__left.png` becomes `<front>__right.png`
    beside it, the name a drawn right view would have; anything else `<name>__mirrored.png`."""
    stem = picture.stem
    if stem.endswith("__left"):
        return picture.with_name(f"{stem[:-len('__left')]}__right.png")
    return picture.with_name(f"{stem}__mirrored.png")


def mirror_work(output: Path, picture: str) -> Callable[[ToolJob], dict[str, Any]]:
    """Flip a picture left to right: a four-view build's right view from its left view."""
    source = inside(output, picture, PICTURES)

    def work(job: ToolJob) -> dict[str, Any]:
        from PIL import Image, ImageOps

        with Image.open(source) as opened:
            if max(opened.size) > 8192:
                raise ValueError("that picture is too large to mirror")
            flipped = ImageOps.mirror(opened.convert("RGBA" if "A" in opened.getbands() else "RGB"))
        target = mirrored_name(source)
        flipped.save(target, "PNG")
        carry_provenance(source, target, "mirror")
        return {"picture": rel(output, target)}
    return work
