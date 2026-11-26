"""Danbooru tag autocomplete and LoRA file metadata."""
import csv
import json
import struct
from functools import lru_cache
from pathlib import Path

from app.core.config import LORAS_DIR, PROJECT_ROOT, TAGS_CSV

COMFY_LORAS_DIR = PROJECT_ROOT / "ComfyUI" / "models" / "loras"

CATEGORY_NAMES = {0: "일반", 1: "작가", 3: "저작권", 4: "캐릭터", 5: "메타"}
# tags that say nothing about a LoRA even when they are frequent in its training set
GENERIC_TAGS = {
    "1girl", "solo", "1boy", "looking at viewer", "simple background", "white background", "smile", "blush",
    "open mouth", "long hair", "short hair", "breasts", "multiple girls", "2girls", "highres", "absurdres",
}


class _Tag:
    __slots__ = ("name", "display", "category", "count", "aliases")

    def __init__(self, name: str, category: int, count: int, aliases: list[str]):
        self.name = name
        self.display = name.replace("_", " ")
        self.category = category
        self.count = count
        self.aliases = aliases


@lru_cache(maxsize=1)
def _load_tags() -> list[_Tag]:
    if not TAGS_CSV.exists():
        return []
    tags: list[_Tag] = []
    with TAGS_CSV.open(encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 3:
                continue
            try:
                category, count = int(row[1]), int(row[2])
            except ValueError:
                continue
            aliases = [a.strip() for a in row[3].split(",") if a.strip()] if len(row) > 3 else []
            tags.append(_Tag(row[0], category, count, aliases))
    tags.sort(key=lambda t: -t.count)
    return tags


def tags_available() -> bool:
    return bool(_load_tags())


def search_tags(query: str, limit: int = 8, categories: set[int] | None = None) -> list[dict]:
    """Prefix search on the tag and its aliases, most used first; spaces and underscores are equivalent."""
    q = query.strip().lower().replace(" ", "_")
    if len(q) < 2:
        return []
    results: list[dict] = []
    seen: set[str] = set()
    # pass 1: the tag itself starts with the query; pass 2: an alias does; pass 3: the query is inside the tag
    for mode in ("prefix", "alias", "contains"):
        for t in _load_tags():
            if categories is not None and t.category not in categories:
                continue
            if t.name in seen:
                continue
            via = None
            if mode == "prefix" and t.name.startswith(q):
                pass
            elif mode == "alias":
                via = next((a for a in t.aliases if a.lower().startswith(q)), None)
                if via is None:
                    continue
            elif mode == "contains" and len(q) >= 3 and q in t.name:
                pass
            else:
                continue
            seen.add(t.name)
            results.append({
                "tag": t.display,
                "category": CATEGORY_NAMES.get(t.category, str(t.category)),
                "count": t.count,
                "alias": via.replace("_", " ") if via else None,
            })
            if len(results) >= limit:
                return results
    return results


# ---------------------------------------------------------------- LoRA metadata
def _lora_path(name: str) -> Path | None:
    for base in (LORAS_DIR, COMFY_LORAS_DIR):
        p = (base / name).resolve()
        if p.is_file() and base.resolve() in p.parents:
            return p
    return None


def _read_safetensors_metadata(path: Path) -> dict:
    with path.open("rb") as f:
        header_len = struct.unpack("<Q", f.read(8))[0]
        if header_len > 100_000_000:
            return {}
        header = json.loads(f.read(header_len))
    return header.get("__metadata__", {}) or {}


@lru_cache(maxsize=256)
def _lora_info_cached(path_str: str, mtime: float) -> dict:
    meta = _read_safetensors_metadata(Path(path_str))
    counts: dict[str, int] = {}
    raw = meta.get("ss_tag_frequency")
    if raw:
        try:
            freq = json.loads(raw)
            for dataset in freq.values():
                for tag, n in dataset.items():
                    counts[tag.strip()] = counts.get(tag.strip(), 0) + int(n)
        except (ValueError, AttributeError):
            counts = {}
    top = sorted(counts.items(), key=lambda kv: -kv[1])
    total = max((n for _, n in top), default=0)
    return {
        "title": meta.get("modelspec.title") or meta.get("ss_output_name") or "",
        "base_model": meta.get("ss_base_model_version") or meta.get("modelspec.architecture") or "",
        "network_dim": meta.get("ss_network_dim") or "",
        "images": meta.get("ss_num_train_images") or "",
        "top_tags": [{"tag": t, "count": n} for t, n in top[:20]],
        # candidates only: frequent, non-generic tags. A LoRA's real trigger is not stored in the file.
        "trigger_candidates": [t for t, n in top if t.lower() not in GENERIC_TAGS and n >= max(3, total * 0.3)][:5],
    }


def lora_info(name: str) -> dict:
    path = _lora_path(name)
    if path is None:
        return {"name": name, "found": False}
    try:
        info = _lora_info_cached(str(path), path.stat().st_mtime)
    except (OSError, ValueError, struct.error):
        return {"name": name, "found": True, "readable": False}
    return {"name": name, "found": True, "readable": True, **info, "has_tag_info": bool(info["top_tags"])}
