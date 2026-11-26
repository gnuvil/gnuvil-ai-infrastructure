from fastapi import APIRouter, Query

from app.services.request_logging import clear_generation_requests, list_generation_requests
from app.services import db

router = APIRouter()


@router.get("/request-logs")
async def get_request_logs(limit: int = Query(default=100, ge=1, le=500)):
    requests = list_generation_requests(limit)
    generation_ids = [item["generation_id"] for item in requests if item.get("generation_id") is not None]
    generations = db.get_generations(generation_ids)
    for item in requests:
        generation_id = item.get("generation_id")
        row = generations.get(generation_id) if generation_id is not None else None
        item["result"] = (
            {
                "id": row["id"],
                "status": row["status"],
                "image_path": row["image_path"],
                "media_type": row["media_type"],
            }
            if row is not None
            else None
        )
    return {"requests": requests}


@router.delete("/request-logs")
async def delete_request_logs():
    return {"cleared": clear_generation_requests()}
