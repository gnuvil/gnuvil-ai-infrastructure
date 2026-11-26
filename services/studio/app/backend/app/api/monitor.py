from datetime import datetime

from fastapi import APIRouter, Query

from app.services.request_logging import clear_generation_requests, list_generation_requests
from app.services import db

router = APIRouter()


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)
    except ValueError:
        return None


def _timings(row) -> dict:
    """Queue wait, generation time and total, in seconds (None while unknown)."""
    keys = row.keys()
    created = row["created_at"] if "created_at" in keys else None
    started = row["started_at"] if "started_at" in keys else None
    finished = row["finished_at"] if "finished_at" in keys else None
    return {
        "created_at": created,
        "started_at": started,
        "finished_at": finished,
        "wait_seconds": _seconds(created, started),
        "run_seconds": _seconds(started, finished),
        "total_seconds": _seconds(created, finished),
    }


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
                **_timings(row),
            }
            if row is not None
            else None
        )
    return {"requests": requests}


@router.delete("/request-logs")
async def delete_request_logs():
    return {"cleared": clear_generation_requests()}
