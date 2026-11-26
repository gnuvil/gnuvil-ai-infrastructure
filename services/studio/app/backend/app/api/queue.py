from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.services import queue_service, db

router = APIRouter()

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@router.get("/queue")
async def get_queue():
    return queue_service.get_queue_snapshot()


@router.post("/cancel/{generation_id}")
async def cancel(generation_id: int):
    row = db.get_generation(generation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Generation not found")
    if row["status"] not in ("queued", "running"):
        raise HTTPException(status_code=400, detail=f"취소할 수 없는 상태입니다: {row['status']}")

    ok = await queue_service.request_cancel(generation_id)
    if not ok:
        raise HTTPException(status_code=409, detail="이미 종료된 작업입니다.")
    return {"generation_id": generation_id, "status": "cancelling"}


@router.websocket("/ws/progress/{generation_id}")
async def ws_progress(websocket: WebSocket, generation_id: int):
    await websocket.accept()

    row = db.get_generation(generation_id)
    if row is None:
        await websocket.send_json({"type": "error", "message": "Generation not found"})
        await websocket.close()
        return

    if row["status"] in TERMINAL_STATUSES:
        await websocket.send_json({"type": row["status"], "image_path": row["image_path"]})
        await websocket.close()
        return

    q = queue_service.subscribe(generation_id)
    try:
        await websocket.send_json({"type": "status", "status": row["status"]})
        pos = queue_service.queue_position(generation_id)
        if row["status"] == "queued" and pos is not None:
            await websocket.send_json({"type": "queue", "ahead": pos})
        while True:
            message = await q.get()
            await websocket.send_json(message)
            if message.get("type") in TERMINAL_STATUSES:
                break
    except WebSocketDisconnect:
        pass
    finally:
        queue_service.unsubscribe(generation_id, q)
        await websocket.close()
