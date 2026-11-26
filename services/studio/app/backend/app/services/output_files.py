"""Keeps generated files reachable: results are served from OUTPUTS_DIR, but ComfyUI writes its own copy first."""
import logging
import shutil
from pathlib import Path

from app.core.config import COMFYUI_OUTPUT_DIR, OUTPUTS_DIR
from app.services import db

logger = logging.getLogger(__name__)


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
