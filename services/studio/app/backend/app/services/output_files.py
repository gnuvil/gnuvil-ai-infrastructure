"""Keeps generated files reachable: results are served from OUTPUTS_DIR, but ComfyUI writes its own copy first.

Also tracks which files each generation produced and deletes them all (app copy and ComfyUI's copy).
"""
import contextvars
import logging
import shutil
from pathlib import Path

from app.core.config import COMFYUI_OUTPUT_DIR, OUTPUTS_DIR
from app.services import db

logger = logging.getLogger(__name__)

# Set by the queue worker while a job runs, so every file saved during that job (including
# post-processing passes) is recorded against its generation.
current_generation: contextvars.ContextVar[int | None] = contextvars.ContextVar("current_generation", default=None)


def find_result(name: str) -> Path | None:
    """The file in OUTPUTS_DIR, or ComfyUI's own copy (root or video/) when the app copy is missing."""
    name = Path(name).name
    for base in (OUTPUTS_DIR, COMFYUI_OUTPUT_DIR, COMFYUI_OUTPUT_DIR / "video"):
        p = base / name
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def reconcile_outputs() -> int:
    """Copies (never moves or deletes) results of completed generations that are missing from OUTPUTS_DIR."""
    restored = 0
    for row in db.list_generations(limit=2000):
        if row["status"] != "completed" or not row["image_path"]:
            continue
        name = Path(row["image_path"]).name
        target = OUTPUTS_DIR / name
        if target.exists():
            continue
        source = find_result(name)
        if source is not None and source != target:
            try:
                shutil.copyfile(source, target)
                restored += 1
            except OSError:
                logger.warning("could not restore %s", name)
    if restored:
        logger.info("restored %s missing result file(s) into %s", restored, OUTPUTS_DIR)
    return restored


def record_saved(image_info: dict, app_path: Path) -> None:
    """Remember that the running generation produced ComfyUI file `image_info` and app copy `app_path`."""
    generation_id = current_generation.get()
    if generation_id is None:
        return
    comfy_path = ""
    if image_info.get("type") == "output":
        comfy_path = str(Path(image_info.get("subfolder") or "") / image_info["filename"]).replace("\\", "/")
    try:
        db.add_generation_file(generation_id, comfy_path, app_path.name)
    except Exception:
        logger.warning("could not record output file for generation %s", generation_id)


def _inside(base: Path, rel: str) -> Path | None:
    """`base/rel` only if it stays inside `base` (no absolute paths or ..)."""
    if not rel or Path(rel).is_absolute():
        return None
    base = base.resolve()
    p = (base / rel).resolve()
    return p if base in p.parents else None


def _candidates(row, recorded) -> tuple[set[str], set[str]]:
    """(app file names, ComfyUI-relative paths) that belong to a generation."""
    app: set[str] = set()
    comfy: set[str] = set()
    final = Path(row["image_path"] or "").name
    if final:
        app.add(final)
    for r in recorded:
        if r["app_name"]:
            app.add(Path(r["app_name"]).name)
        if r["comfy_path"]:
            comfy.add(r["comfy_path"])
    # Older generations have no records: derive ComfyUI's copy from the app name.
    for name in list(app):
        stem, suffix = Path(name).stem, Path(name).suffix
        raw = [name]
        if stem.endswith("_ctx"):  # inpaint paste-back of ComfyUI's crop result
            raw.append(stem[: -len("_ctx")] + ".png")
            app.add(stem[: -len("_ctx")] + ".png")
        if stem.endswith("_ext"):  # joined video segments; the first clip keeps ComfyUI's name
            raw.append(stem[: -len("_ext")] + suffix)
            app.add(stem[: -len("_ext")] + suffix)
        for n in raw:
            comfy.add(n)
            comfy.add(f"video/{n}")
    return app, comfy


def delete_generation_files(row) -> list[str]:
    """Deletes every file of a generation from the app outputs and ComfyUI's output folder.

    Returns the deleted paths (relative, prefixed with app/ or comfyui/). Missing files are skipped.
    """
    app, comfy = _candidates(row, db.list_generation_files(row["id"]))
    removed: list[str] = []
    for label, base, names in (("app", OUTPUTS_DIR, app), ("comfyui", COMFYUI_OUTPUT_DIR, comfy)):
        for rel in sorted(names):
            p = _inside(base, rel)
            if p is None or not p.is_file():
                continue
            try:
                p.unlink()
                removed.append(f"{label}/{rel}")
            except OSError as e:
                logger.warning("could not delete %s: %s", p, e)
    return removed
