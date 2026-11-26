import json
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import OUTPUTS_DIR, UPLOADS_DIR
from app.services import characters_service as chars
from app.services import db

router = APIRouter()

REF_DIR = UPLOADS_DIR / "characters"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _parse_loras(raw: str) -> list:
    try:
        v = json.loads(raw or "[]")
        if not isinstance(v, list):
            raise ValueError
        return v
    except ValueError:
        raise HTTPException(status_code=400, detail="loras 필드는 [{name, weight}] 형식의 JSON이어야 합니다.")


async def _save_reference(file: UploadFile, name: str) -> str:
    ext = Path(file.filename or "ref.png").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="참조 이미지는 png, jpg, webp만 지원합니다.")
    REF_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "", name)[:20] or "char"
    dest = REF_DIR / f"{slug}_{int(time.time())}{ext}"
    dest.write_bytes(await file.read())
    return f"/uploads/characters/{dest.name}"


def _delete_reference(url: Optional[str]) -> None:
    if url and url.startswith("/uploads/characters/"):
        (REF_DIR / Path(url).name).unlink(missing_ok=True)


@router.get("/characters")
async def list_characters():
    return {"characters": chars.list_characters()}


@router.post("/characters")
async def create_character(
    name: str = Form(...),
    aliases: str = Form(""),
    tags: str = Form(""),
    loras: str = Form("[]"),
    use_reference: bool = Form(False),
    ip_weight: float = Form(0.2),
    notes: str = Form(""),
    file: Optional[UploadFile] = None,
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="이름이 비어 있습니다.")
    ref = await _save_reference(file, name) if file else None
    try:
        cid = chars.create_character(name, aliases, tags, _parse_loras(loras), ref, use_reference, ip_weight, notes)
    except sqlite3.IntegrityError:
        _delete_reference(ref)
        raise HTTPException(status_code=409, detail=f"'{name.strip()}' 이름의 캐릭터가 이미 있습니다.")
    return chars.get_character(cid)


@router.put("/characters/{character_id}")
async def update_character(
    character_id: int,
    name: Optional[str] = Form(None),
    aliases: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    loras: Optional[str] = Form(None),
    use_reference: Optional[bool] = Form(None),
    ip_weight: Optional[float] = Form(None),
    notes: Optional[str] = Form(None),
    remove_reference: bool = Form(False),
    file: Optional[UploadFile] = None,
):
    current = chars.get_character(character_id)
    if current is None:
        raise HTTPException(status_code=404, detail="캐릭터를 찾을 수 없습니다.")
    fields = {
        "name": name, "aliases": aliases, "tags": tags, "use_reference": use_reference,
        "ip_weight": ip_weight, "notes": notes,
        "loras": _parse_loras(loras) if loras is not None else None,
    }
    if file is not None:
        fields["reference_image"] = await _save_reference(file, name or current["name"])
        _delete_reference(current["reference_image"])
    elif remove_reference:
        fields["reference_image"] = ""
        _delete_reference(current["reference_image"])
    try:
        chars.update_character(character_id, **fields)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="같은 이름의 캐릭터가 이미 있습니다.")
    updated = chars.get_character(character_id)
    if updated and updated["reference_image"] == "":
        chars.update_character(character_id, reference_image=None)
        updated["reference_image"] = None
    return updated


@router.delete("/characters/{character_id}")
async def delete_character(character_id: int):
    current = chars.get_character(character_id)
    if current is None:
        raise HTTPException(status_code=404, detail="캐릭터를 찾을 수 없습니다.")
    chars.delete_character(character_id)
    _delete_reference(current["reference_image"])
    return {"deleted": character_id}


class FromGeneration(BaseModel):
    generation_id: int
    name: str
    aliases: str = ""
    strip_prefix: str = ""


@router.post("/characters/from_generation")
async def character_from_generation(req: FromGeneration):
    """Save a generated image's tags, LoRAs and the image itself as a new character."""
    row = db.get_generation(req.generation_id)
    if row is None or not row["image_path"]:
        raise HTTPException(status_code=404, detail="생성 기록을 찾을 수 없습니다.")
    if row["media_type"] == "video":
        raise HTTPException(status_code=400, detail="영상에서는 캐릭터를 만들 수 없습니다. 이미지를 선택하세요.")
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="이름이 비어 있습니다.")

    prompt = row["prompt"]
    prefix = req.strip_prefix.strip()
    if prefix and prompt.startswith(prefix):
        prompt = prompt[len(prefix):].strip()
    try:
        loras = json.loads(row["loras"] or "[]")
    except ValueError:
        loras = []

    src = OUTPUTS_DIR / Path(row["image_path"]).name
    ref = None
    if src.exists():
        REF_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "", req.name)[:20] or "char"
        dest = REF_DIR / f"{slug}_{int(time.time())}{src.suffix}"
        shutil.copyfile(src, dest)
        ref = f"/uploads/characters/{dest.name}"
    try:
        cid = chars.create_character(req.name, req.aliases, prompt, loras, ref, False, 0.2, f"생성 #{req.generation_id}에서 저장")
    except sqlite3.IntegrityError:
        _delete_reference(ref)
        raise HTTPException(status_code=409, detail=f"'{req.name.strip()}' 이름의 캐릭터가 이미 있습니다.")
    return chars.get_character(cid)
