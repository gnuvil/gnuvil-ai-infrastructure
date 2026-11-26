import io
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile
from PIL import Image, ImageStat

from app.core.comfyui_client import comfyui_client
from app.core.config import UPLOADS_DIR, OUTPUTS_DIR
from app.models.schemas import InpaintParams, GenerateResponse, LoRAConfig
from app.services.workflow_service import build_inpaint_workflow
from app.services import inpaint_context
from app.services.generation_service import run_workflow_and_save
from app.services import db, queue_service
from app.services.request_logging import log_generation_request

router = APIRouter()

EMPTY_REGION_STD = 12.0
LARGE_MASK_RATIO = 0.08


def resolve_inpaint_mode(mode: str, image_bytes: bytes, mask_bytes: bytes) -> tuple[str, str]:
    """'auto' -> 'fill' when the masked area is nearly uniform (nothing to preserve, e.g. adding
    an object to empty background) or large; otherwise 'keep' for small detail fixes."""
    if mode != "auto":
        return mode, ""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        # Same convention as the workflow (LoadImageMask, red channel): white = repaint.
        m = Image.open(io.BytesIO(mask_bytes)).convert("RGB").getchannel("R")
        m = m.resize(img.size).point(lambda v: 255 if v > 127 else 0)
        stat = ImageStat.Stat(img, mask=m)
        count = stat.count[0]
        if count == 0:
            return "keep", ""
        ratio = count / (img.size[0] * img.size[1])
        std = stat.stddev[0]
    except Exception:
        return "keep", ""
    if std < EMPTY_REGION_STD:
        return "fill", "선택 영역이 비어 있어(단색에 가까움) 완전 교체 모드로 처리합니다."
    if ratio >= LARGE_MASK_RATIO:
        return "fill", "선택 영역이 넓어 완전 교체 모드로 처리합니다."
    return "keep", ""


@router.post("/inpaint", response_model=GenerateResponse)
async def inpaint(
    prompt: str = Form(...),
    negative_prompt: str = Form(""),
    checkpoint: str = Form(...),
    seed: Optional[int] = Form(None),
    steps: int = Form(28),
    cfg: float = Form(5.0),
    sampler: str = Form("euler"),
    denoise: float = Form(0.85),
    sampling: str = Form("eps"),
    grow_mask_by: int = Form(6),
    mask_blur: int = Form(16),
    inpaint_mode: str = Form("auto"),
    context_crop: str = Form("auto"),
    harmonize: bool = Form(False),
    harmonize_denoise: float = Form(0.3),
    loras: str = Form("[]"),
    vae: Optional[str] = Form(None),
    face_detailer: bool = Form(False),
    face_detailer_model: Optional[str] = Form(None),
    face_detailer_denoise: float = Form(0.5),
    parent_image_id: Optional[int] = Form(None),
    source_generation_id: Optional[int] = Form(None),
    file: Optional[UploadFile] = None,
    mask_file: UploadFile = None,
):
    if not await comfyui_client.is_available():
        raise HTTPException(
            status_code=503,
            detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.",
        )

    if file is None and source_generation_id is None:
        raise HTTPException(status_code=400, detail="파일 업로드 또는 source_generation_id 중 하나가 필요합니다.")
    if mask_file is None:
        raise HTTPException(status_code=400, detail="마스크 이미지가 필요합니다.")

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

    mask_content = await mask_file.read()
    inpaint_mode, _mode_note = resolve_inpaint_mode(inpaint_mode, content, mask_content)
    mask_filename = mask_file.filename or "mask.png"

    try:
        lora_list = [LoRAConfig(**l) for l in json.loads(loras)]
    except (json.JSONDecodeError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="loras 필드는 [{name, weight}] 형식의 JSON이어야 합니다.")

    params = InpaintParams(
        prompt=prompt,
        negative_prompt=negative_prompt,
        checkpoint=checkpoint,
        seed=seed,
        steps=steps,
        cfg=cfg,
        sampler=sampler,
        denoise=denoise,
        sampling=sampling,
        grow_mask_by=grow_mask_by,
        mask_blur=mask_blur,
        inpaint_mode=inpaint_mode,
        loras=lora_list,
        vae=vae,
        face_detailer=face_detailer,
        face_detailer_model=face_detailer_model,
        face_detailer_denoise=face_detailer_denoise,
        parent_image_id=parent_image_id,
    )
    log_generation_request(
        "/api/inpaint",
        params,
        source_generation_id=source_generation_id,
        uploaded_file=file.filename if file else None,
        mask_file=mask_filename,
        context_crop=context_crop,
        harmonize=harmonize,
        harmonize_denoise=harmonize_denoise,
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

    post_process = None
    plan = None
    orig_img = None
    mask_img = None
    if context_crop != "off":
        try:
            orig_img = Image.open(io.BytesIO(content)).convert("RGB")
            mask_img = inpaint_context.load_mask(mask_content, orig_img.size)
            plan = inpaint_context.plan_crop(mask_img, auto=(context_crop == "auto"))
        except Exception:
            plan = None

    if plan is not None:
        crop_bytes, crop_mask_bytes = inpaint_context.crop_for_sampling(orig_img, mask_img, plan)
        image_upload_result = await comfyui_client.upload_image(f"crop_{filename}.png", crop_bytes)
        mask_upload_result = await comfyui_client.upload_image(f"cropmask_{filename}.png", crop_mask_bytes)
        workflow, seed_used = build_inpaint_workflow(params, image_upload_result["name"], mask_upload_result["name"])

        async def post_process(result_path: Path, _plan=plan, _orig=orig_img, _mask=mask_img, _params=params, _seed=seed_used):
            generated = Image.open(result_path).convert("RGB")
            composed = inpaint_context.paste_back(_orig, generated, _mask, _plan, feather=max(4, _params.mask_blur // 2))
            if harmonize:
                # re-sample a soft band around the pasted region at low denoise so colour/lighting match
                band = inpaint_context.harmonize_mask(_mask.crop(_plan.box))
                full_band = Image.new("L", _orig.size, 0)
                full_band.paste(band, _plan.box[:2])
                blend_crop, _ = inpaint_context.crop_for_sampling(composed, full_band, _plan)
                blend_mask = full_band.crop(_plan.box).resize(_plan.size, Image.LANCZOS)
                mask_bytes = io.BytesIO()
                blend_mask.convert("RGB").save(mask_bytes, "PNG")
                up_img = await comfyui_client.upload_image(f"harm_{filename}.png", blend_crop)
                up_mask = await comfyui_client.upload_image(f"harmmask_{filename}.png", mask_bytes.getvalue())
                h_params = _params.model_copy(update={
                    "denoise": harmonize_denoise, "inpaint_mode": "keep", "grow_mask_by": 0,
                    "mask_blur": 24, "seed": _seed,
                })
                h_workflow, _ = build_inpaint_workflow(h_params, up_img["name"], up_mask["name"])
                h_path = await run_workflow_and_save(h_workflow)
                harmonized = Image.open(h_path).convert("RGB")
                composed = inpaint_context.paste_back(composed, harmonized, full_band, _plan, feather=0)
            OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
            out_path = OUTPUTS_DIR / f"{result_path.stem}_ctx.png"
            composed.save(out_path)
            return out_path
    else:
        image_upload_result = await comfyui_client.upload_image(filename, content)
        mask_upload_result = await comfyui_client.upload_image(f"mask_{mask_filename}", mask_content)
        workflow, seed_used = build_inpaint_workflow(
            params, image_upload_result["name"], mask_upload_result["name"]
        )

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

    await queue_service.enqueue(generation_id, workflow, post_process)

    return GenerateResponse(generation_id=generation_id, prompt_id="", status="queued")
