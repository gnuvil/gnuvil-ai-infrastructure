import io
import json
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile
from PIL import Image

from app.core.comfyui_client import comfyui_client, ComfyUIUnavailableError
from app.models.schemas import ControlNetGenerateParams, GenerateResponse, LoRAConfig
from app.services import db, queue_service
from app.services.generation_service import run_workflow_and_save
from app.services.workflow_service import build_controlnet_workflow, build_pose_preprocess_node, load_workflow
from app.services.request_logging import log_generation_request

router = APIRouter()


def match_reference_aspect(content: bytes, width: int, height: int) -> tuple[int, int]:
    """ControlNet stretches its hint to the latent size, so a reference whose aspect differs
    from the requested size gets distorted. Keep the requested pixel area but use the
    reference's aspect ratio (multiples of 64) when they differ by more than 10%."""
    try:
        with Image.open(io.BytesIO(content)) as im:
            ref_w, ref_h = im.size
    except Exception:
        return width, height
    if ref_w <= 0 or ref_h <= 0:
        return width, height
    ref_ratio = ref_w / ref_h
    if abs(ref_ratio / (width / height) - 1) <= 0.10:
        return width, height
    area = width * height
    new_h = round((area / ref_ratio) ** 0.5 / 64) * 64
    new_w = round(new_h * ref_ratio / 64) * 64
    return max(64, min(2048, new_w)), max(64, min(2048, new_h))


@router.post("/preprocess/canny")
async def preprocess_canny(
    file: UploadFile,
    low_threshold: float = Form(0.4),
    high_threshold: float = Form(0.8),
):
    if not await comfyui_client.is_available():
        raise HTTPException(
            status_code=503,
            detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.",
        )

    content = await file.read()
    filename = file.filename or "upload.png"
    upload_result = await comfyui_client.upload_image(filename, content)

    workflow = load_workflow("canny_preprocess")
    workflow["1"]["inputs"]["image"] = upload_result["name"]
    workflow["2"]["inputs"]["low_threshold"] = low_threshold
    workflow["2"]["inputs"]["high_threshold"] = high_threshold

    try:
        image_path = await run_workflow_and_save(workflow)
    except (ComfyUIUnavailableError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"image_path": f"/outputs/{image_path.name}"}


@router.post("/preprocess/openpose")
async def preprocess_openpose(file: UploadFile):
    if not await comfyui_client.is_available():
        raise HTTPException(
            status_code=503,
            detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.",
        )

    content = await file.read()
    upload_result = await comfyui_client.upload_image(file.filename or "upload.png", content)

    workflow = load_workflow("canny_preprocess")
    workflow["1"]["inputs"]["image"] = upload_result["name"]
    workflow["2"] = build_pose_preprocess_node(["1", 0])
    workflow["3"]["inputs"]["filename_prefix"] = "local_image_ai_pose"

    try:
        image_path = await run_workflow_and_save(workflow)
    except (ComfyUIUnavailableError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"image_path": f"/outputs/{image_path.name}"}


@router.post("/controlnet_generate", response_model=GenerateResponse)
async def controlnet_generate(
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
    controlnet_model: str = Form(...),
    preprocessor: str = Form("canny"),
    canny_low_threshold: float = Form(0.4),
    canny_high_threshold: float = Form(0.8),
    strength: float = Form(0.85),
    start_percent: float = Form(0.0),
    end_percent: float = Form(0.7),
    face_detailer: bool = Form(False),
    face_detailer_model: Optional[str] = Form(None),
    face_detailer_denoise: float = Form(0.5),
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

    params = ControlNetGenerateParams(
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
        controlnet_model=controlnet_model,
        preprocessor=preprocessor,
        canny_low_threshold=canny_low_threshold,
        canny_high_threshold=canny_high_threshold,
        strength=strength,
        start_percent=start_percent,
        end_percent=end_percent,
        face_detailer=face_detailer,
        face_detailer_model=face_detailer_model,
        face_detailer_denoise=face_detailer_denoise,
        parent_image_id=parent_image_id,
    )

    checkpoints = await comfyui_client.get_checkpoints()
    if params.checkpoint not in checkpoints:
        raise HTTPException(
            status_code=400,
            detail=f"체크포인트 '{params.checkpoint}'를 찾을 수 없습니다. 사용 가능한 체크포인트: {checkpoints}",
        )

    controlnets = await comfyui_client.get_controlnets()
    if params.controlnet_model not in controlnets:
        raise HTTPException(
            status_code=400,
            detail=f"ControlNet 모델 '{params.controlnet_model}'을 찾을 수 없습니다. "
            f"models/controlnet 폴더에 파일을 추가하세요. 사용 가능한 목록: {controlnets}",
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

    content = await file.read()
    filename = file.filename or "control.png"
    upload_result = await comfyui_client.upload_image(filename, content)

    params.width, params.height = match_reference_aspect(content, params.width, params.height)
    log_generation_request(
        "/api/controlnet_generate",
        params,
        uploaded_file=filename,
    )

    workflow, seed_used = build_controlnet_workflow(params, upload_result["name"])

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
