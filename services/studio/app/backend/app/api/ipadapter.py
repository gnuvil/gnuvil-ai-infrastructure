import json
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile

from app.core.comfyui_client import comfyui_client
from app.models.schemas import GenerateResponse, IPAdapterGenerateParams, LoRAConfig
from app.services import db, queue_service
from app.services.workflow_service import build_ipadapter_workflow
from app.services.request_logging import log_generation_request

router = APIRouter()


@router.post("/ipadapter_generate", response_model=GenerateResponse)
async def ipadapter_generate(
    file: UploadFile,
    prompt: str = Form(...),
    negative_prompt: str = Form(""),
    checkpoint: str = Form(...),
    seed: Optional[int] = Form(None),
    steps: int = Form(28),
    cfg: float = Form(5.0),
    sampler: str = Form("euler"),
    width: int = Form(1024),
    height: int = Form(1024),
    sampling: str = Form("eps"),
    loras: str = Form("[]"),
    vae: Optional[str] = Form(None),
    hires_fix: bool = Form(False),
    hires_scale: float = Form(1.5),
    hires_denoise: float = Form(0.5),
    face_detailer: bool = Form(False),
    face_detailer_model: Optional[str] = Form(None),
    face_detailer_denoise: float = Form(0.5),
    ipadapter_model: str = Form(...),
    clip_vision_model: str = Form(...),
    weight: float = Form(0.2),
    weight_type: str = Form("linear"),
    start_at: float = Form(0.0),
    end_at: float = Form(1.0),
    parent_image_id: Optional[int] = Form(None),
):
    if not await comfyui_client.is_available():
        raise HTTPException(
            status_code=503,
            detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.",
        )

    try:
        lora_list = [LoRAConfig(**l) for l in json.loads(loras)]
    except (json.JSONDecodeError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="loras 필드는 [{name, weight}] 형식의 JSON이어야 합니다.")

    params = IPAdapterGenerateParams(
        prompt=prompt,
        negative_prompt=negative_prompt,
        checkpoint=checkpoint,
        seed=seed,
        steps=steps,
        cfg=cfg,
        sampler=sampler,
        width=width,
        height=height,
        sampling=sampling,
        loras=lora_list,
        vae=vae,
        hires_fix=hires_fix,
        hires_scale=hires_scale,
        hires_denoise=hires_denoise,
        face_detailer=face_detailer,
        face_detailer_model=face_detailer_model,
        face_detailer_denoise=face_detailer_denoise,
        ipadapter_model=ipadapter_model,
        clip_vision_model=clip_vision_model,
        weight=weight,
        weight_type=weight_type,
        start_at=start_at,
        end_at=end_at,
        parent_image_id=parent_image_id,
    )

    checkpoints = await comfyui_client.get_checkpoints()
    if params.checkpoint not in checkpoints:
        raise HTTPException(
            status_code=400,
            detail=f"체크포인트 '{params.checkpoint}'를 찾을 수 없습니다. 사용 가능한 체크포인트: {checkpoints}",
        )

    ipadapters = await comfyui_client.get_ipadapters()
    if params.ipadapter_model not in ipadapters:
        raise HTTPException(
            status_code=400,
            detail=f"IP-Adapter 모델 '{params.ipadapter_model}'을 찾을 수 없습니다. "
            f"models/ipadapter 폴더에 파일을 추가하세요. 사용 가능한 목록: {ipadapters}",
        )

    clip_visions = await comfyui_client.get_clip_visions()
    if params.clip_vision_model not in clip_visions:
        raise HTTPException(
            status_code=400,
            detail=f"CLIP Vision 모델 '{params.clip_vision_model}'을 찾을 수 없습니다. "
            f"models/clip_vision 폴더에 파일을 추가하세요. 사용 가능한 목록: {clip_visions}",
        )

    if params.face_detailer:
        detectors = await comfyui_client.get_bbox_detectors()
        wanted = [m.strip() for m in (params.face_detailer_model or "").split(",") if m.strip()]
        if not wanted or any(m not in detectors for m in wanted):
            raise HTTPException(
                status_code=400,
                detail=f"얼굴 감지 모델 '{params.face_detailer_model}'을 찾을 수 없습니다. "
                f"models/ultralytics/bbox 폴더에 파일을 추가하세요. 사용 가능한 목록: {detectors}",
            )

    if params.loras:
        available_loras = await comfyui_client.get_loras()
        missing = [l.name for l in params.loras if l.name not in available_loras]
        if missing:
            raise HTTPException(status_code=400, detail=f"LoRA를 찾을 수 없습니다: {missing}")

    content = await file.read()
    upload_result = await comfyui_client.upload_image(file.filename or "reference.png", content)
    log_generation_request(
        "/api/ipadapter_generate",
        params,
        uploaded_file=file.filename or "reference.png",
    )

    workflow, seed_used = build_ipadapter_workflow(params, upload_result["name"])

    generation_id = db.create_generation(
        prompt=params.prompt,
        negative_prompt=params.negative_prompt,
        model=params.checkpoint,
        loras=[l.model_dump() for l in params.loras],
        seed=seed_used,
        steps=params.steps,
        cfg=params.cfg,
        width=params.width,
        height=params.height,
        sampler=params.sampler,
        parent_image_id=params.parent_image_id,
        status="queued",
    )

    await queue_service.enqueue(generation_id, workflow)

    return GenerateResponse(generation_id=generation_id, prompt_id="", status="queued")
