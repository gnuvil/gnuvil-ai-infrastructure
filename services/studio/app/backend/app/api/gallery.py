"""Browse and delete generated results (the app copy and ComfyUI's own copy)."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import OUTPUTS_DIR
from app.services import db, output_files, queue_service

router = APIRouter()


def _item(row) -> dict:
    name = Path(row["image_path"]).name
    path = OUTPUTS_DIR / name
    size = path.stat().st_size if path.is_file() else None
    return {
        "id": row["id"],
        "image_path": row["image_path"],
        "media_type": row["media_type"],
        "prompt": row["prompt"],
        "negative_prompt": row["negative_prompt"],
        "model": row["model"],
        "seed": row["seed"],
        "steps": row["steps"],
        "cfg": row["cfg"],
        "sampler": row["sampler"],
        "width": row["width"],
        "height": row["height"],
        "frames": row["frames"],
        "created_at": row["created_at"],
        "file_size": size,
        "file_missing": size is None,
    }


@router.get("/gallery")
async def gallery(offset: int = Query(0, ge=0), limit: int = Query(60, ge=1, le=200), media: str | None = None):
    rows, total = db.list_generations_page(offset, limit, media)
    return {"total": total, "offset": offset, "items": [_item(r) for r in rows]}


class DeleteRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


def _delete_one(generation_id: int) -> dict:
    row = db.get_generation(generation_id)
    if row is None:
        return {"id": generation_id, "deleted": False, "reason": "기록이 없습니다."}
    snapshot = queue_service.get_queue_snapshot()
    if row["status"] in ("queued", "running") or generation_id == snapshot["current"] or generation_id in snapshot["queued"]:
        return {"id": generation_id, "deleted": False, "reason": "생성 중인 작업은 지울 수 없습니다."}
    removed = output_files.delete_generation_files(row)
    db.delete_generation(generation_id)
    return {"id": generation_id, "deleted": True, "files": removed}


@router.post("/gallery/delete")
async def delete_generations(req: DeleteRequest):
    """Deletes generations and every file they produced (app outputs + ComfyUI output folder)."""
    results = [_delete_one(i) for i in dict.fromkeys(req.ids)]
    return {
        "deleted": sum(1 for r in results if r["deleted"]),
        "files": sum(len(r.get("files", [])) for r in results),
        "results": results,
    }


@router.delete("/generation/{generation_id}")
async def delete_generation(generation_id: int):
    result = _delete_one(generation_id)
    if not result["deleted"]:
        raise HTTPException(status_code=409 if "생성 중" in result["reason"] else 404, detail=result["reason"])
    return result
