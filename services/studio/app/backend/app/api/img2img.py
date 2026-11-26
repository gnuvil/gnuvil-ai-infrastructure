import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile

from app.core.comfyui_client import comfyui_client
from app.core.config import UPLOADS_DIR, OUTPUTS_DIR
from app.models.schemas import Img2ImgParams, GenerateResponse, LoRAConfig
from app.services.workflow_service import build_img2img_workflow
from app.services import db, queue_service
from app.services.request_logging import log_generation_request

router = APIRouter()


@router.post("/img2img", response_model=GenerateResponse)
async def img2img(
    prompt: str = Form(...),
    negative_prompt: str = Form(""),
    checkpoint: str = Form(...),
    seed: Optional[int] = Form(None),
    steps: int = Form(28),
    cfg: float = Form(5.0),
    sampler: str = Form("euler"),
    denoise: float = Form(0.6),
    sampling: str = Form("eps"),
    loras: str = Form("[]"),
    vae: Optional[str] = Form(None),
    face_detailer: bool = Form(False),
    face_detailer_model: Optional[str] = Form(None),
    face_detailer_denoise: float = Form(0.5),
    parent_image_id: Optional[int] = Form(None),
    source_generation_id: Optional[int] = Form(None),
    file: Optional[UploadFile] = None,
):
    if not await comfyui_client.is_available():
        raise HTTPException(
            status_code=503,
            detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.",
        )

    if file is None and source_generation_id is None:
        raise HTTPException(status_code=400, detail="파일 업로드 또는 source_generation_id 중 하나가 필요합니다.")

    if file is not None:
        content = await file.read()
        filename = file.filename or "upload.png"
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        (UPLOADS_DIR / filename).write_bytes(content)
    else:
        source = db.get_generation(source_generation_id)
        if source is None or not source["image_path"]:
            raise HTTPException(status_code=404, detail="원본 생성 기록을 찾을 수 없습니다.")
        source_path = OUTPUTS_DIR / Path(source["image_path"]).name
        if not source_path.exists():
            raise HTTPException(status_code=404, detail="원본 이미지 파일을 찾을 수 없습니다.")
        content = source_path.read_bytes()
        filename = source_path.name
        if parent_image_id is None:
            parent_image_id = source_generation_id

    try:
        lora_list = [LoRAConfig(**l) for l in json.loads(loras)]
    except (json.JSONDecodeError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="loras 필드는 [{name, weight}] 형식의 JSON이어야 합니다.")

    params = Img2ImgParams(
        prompt=prompt,
        negative_prompt=negative_prompt,
        checkpoint=checkpoint,
        seed=seed,
        steps=steps,
        cfg=cfg,
        sampler=sampler,
        denoise=denoise,
        sampling=sampling,
        loras=lora_list,
        vae=vae,
        face_detailer=face_detailer,
        face_detailer_model=face_detailer_model,
        face_detailer_denoise=face_detailer_denoise,
        parent_image_id=parent_image_id,
    )
    log_generation_request(
        "/api/img2img",
        params,
        source_generation_id=source_generation_id,
        uploaded_file=file.filename if file else None,
    )

    checkpoints = await comfyui_client.get_checkpoints()
    if params.checkpoint not in checkpoints:
        raise HTTPException(
            status_code=400,
            detail=f"체크포인트 '{params.checkpoint}'를 찾을 수 없습니다. 사용 가능한 체크포인트: {checkpoints}",
        )

    if params.face_detailer:
        detectors = await comfyui_client.get_bbox_detectors()
        wanted = [m.strip() for m in (params.face_detailer_model or "").split(",") if m.strip()]
        if not wanted or any(m not in detectors for m in wanted):
            raise HTTPException(
                status_code=400,
                detail=f"얼굴 감지 모델 '{params.face_detailer_model}'을 찾을 수 없습니다. models/ultralytics/bbox 폴더에 파일을 추가하세요. 사용 가능한 목록: {detectors}",
            )

    if params.loras:
        available_loras = await comfyui_client.get_loras()
        missing = [l.name for l in params.loras if l.name not in available_loras]
        if missing:
            raise HTTPException(status_code=400, detail=f"LoRA를 찾을 수 없습니다: {missing}")

    upload_result = await comfyui_client.upload_image(filename, content)
    comfyui_image_name = upload_result["name"]

    workflow, seed_used = build_img2img_workflow(params, comfyui_image_name)

    generation_id = db.create_generation(
        prompt=params.prompt,
        negative_prompt=params.negative_prompt,
        model=params.checkpoint,
        loras=[l.model_dump() for l in params.loras],
        seed=seed_used,
        steps=params.steps,
        cfg=params.cfg,
        width=0,
        height=0,
        sampler=params.sampler,
        parent_image_id=params.parent_image_id,
        status="queued",
    )

    await queue_service.enqueue(generation_id, workflow)

    return GenerateResponse(generation_id=generation_id, prompt_id="", status="queued")
