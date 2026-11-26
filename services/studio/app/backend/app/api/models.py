from fastapi import APIRouter, HTTPException

from app.core.comfyui_client import comfyui_client
from app.services import db, tags_service, vram_manager

router = APIRouter()


@router.get("/models")
async def get_models():
    if not await comfyui_client.is_available():
        raise HTTPException(status_code=503, detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.")
    return {"checkpoints": await comfyui_client.get_checkpoints()}


@router.get("/loras")
async def get_loras():
    if not await comfyui_client.is_available():
        raise HTTPException(status_code=503, detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.")
    # video acceleration LoRAs (wan*) and Qwen-Image LoRAs live in the same folder but are not SDXL LoRAs
    return {"loras": [n for n in await comfyui_client.get_loras() if not n.lower().startswith("wan") and "qwen" not in n.lower()]}


@router.get("/vaes")
async def get_vaes():
    if not await comfyui_client.is_available():
        raise HTTPException(status_code=503, detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.")
    # video (Wan) and Qwen-Image VAEs share the folder but must not show up as checkpoint-pipeline VAEs
    return {"vaes": [v for v in await comfyui_client.get_vaes() if not v.lower().startswith("wan") and "qwen" not in v.lower()]}


@router.get("/controlnets")
async def get_controlnets():
    if not await comfyui_client.is_available():
        raise HTTPException(status_code=503, detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.")
    return {"controlnets": await comfyui_client.get_controlnets()}


@router.get("/ipadapters")
async def get_ipadapters():
    if not await comfyui_client.is_available():
        raise HTTPException(status_code=503, detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.")
    return {"ipadapters": await comfyui_client.get_ipadapters()}


@router.get("/clip_visions")
async def get_clip_visions():
    if not await comfyui_client.is_available():
        raise HTTPException(status_code=503, detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.")
    return {"clip_visions": await comfyui_client.get_clip_visions()}


@router.get("/bbox_detectors")
async def get_bbox_detectors():
    if not await comfyui_client.is_available():
        raise HTTPException(status_code=503, detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.")
    return {"bbox_detectors": await comfyui_client.get_bbox_detectors()}


@router.get("/history")
async def get_history(limit: int = 50):
    rows = db.list_generations(limit=limit)
    return {"generations": [dict(row) for row in rows]}


@router.get("/generation/{generation_id}")
async def get_generation(generation_id: int):
    row = db.get_generation(generation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Generation not found")
    return dict(row)


@router.get("/gpu")
async def get_gpu_status():
    status = await vram_manager.read_vram()
    if status is None:
        raise HTTPException(status_code=503, detail="GPU 정보를 읽을 수 없습니다. ComfyUI가 꺼져 있을 수 있습니다.")
    return {
        "name": status.name,
        "vram_used_mb": round(status.used_mb, 1),
        "vram_total_mb": round(status.total_mb, 1),
        "utilization_percent": status.utilization_percent,
        "temperature_c": status.temperature_c,
    }


@router.get("/tags")
async def search_tags(q: str = "", limit: int = 8):
    """Danbooru tag autocomplete (English tags; spaces and underscores are treated the same)."""
    return {"available": tags_service.tags_available(), "tags": tags_service.search_tags(q, min(max(limit, 1), 20))}


@router.get("/lora_info")
async def lora_info(name: str):
    """Training-set tag statistics stored in a LoRA file (candidates for trigger words, not guaranteed triggers)."""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="잘못된 LoRA 이름입니다.")
    return tags_service.lora_info(name)
