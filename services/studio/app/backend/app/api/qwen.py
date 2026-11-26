import io
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile
from PIL import Image

from app.core.comfyui_client import comfyui_client
from app.core.config import OUTPUTS_DIR, UPLOADS_DIR
from app.models.schemas import GenerateResponse, LoRAConfig, QwenEditParams, QwenGenerateParams
from app.services import db, queue_service
from app.services.workflow_service import build_qwen_edit_workflow, build_qwen_txt2img_workflow
from app.services.request_logging import log_generation_request

router = APIRouter()

# First load of the 20B GGUF model (dequantizing ~13GB on a 12GB card) can take a while before any
# ComfyUI progress message arrives; this is "seconds without a message", not total job time.
WAIT_TIMEOUT_SECONDS = 900


def _pick(available: list[str], hint: str, label: str, folder: str) -> str:
    candidates = [n for n in available if hint in n.lower()]
    if not candidates:
        raise HTTPException(
            status_code=400,
            detail=f"{label}이(가) 없습니다. {folder} 폴더에 파일을 넣고 ComfyUI를 재시작하세요. (현재 목록: {available})",
        )
    return candidates[0]


def _fit_edit_size(source: tuple[int, int]) -> tuple[int, int]:
    """Keeps the source image's aspect ratio at ~1024x1024 pixels, snapped to multiples of 16."""
    w0, h0 = source
    ratio = w0 / h0
    h = (1024 * 1024 / ratio) ** 0.5
    w = h * ratio
    return max(512, min(1536, round(w / 16) * 16)), max(512, min(1536, round(h / 16) * 16))


async def _pick_models(edit: bool) -> tuple[str, str, str]:
    unets = [n for n in await comfyui_client.get_gguf_unets() if ("edit" in n.lower()) == edit]
    label = "Qwen-Image-Edit 모델" if edit else "Qwen-Image 모델"
    unet = _pick(unets, "qwen", label, "models/diffusion_models")
    # an abliterated Qwen2.5-VL encoder is preferred when present: Edit derives its conditioning
    # from this same VL model, so an uncensored encoder affects both t2i and Edit mode.
    encoders = [n for n in await comfyui_client.get_text_encoders() if "qwen" in n.lower()]
    if not encoders:
        raise HTTPException(
            status_code=400,
            detail=f"텍스트 인코더이(가) 없습니다. models/text_encoders 폴더에 파일을 넣고 ComfyUI를 재시작하세요. (현재 목록: {await comfyui_client.get_text_encoders()})",
        )
    encoder = next((n for n in encoders if "ablit" in n.lower()), encoders[0])
    vae = _pick(await comfyui_client.get_vaes(), "qwen", "VAE", "models/vae")
    return unet, encoder, vae


async def _qwen_loras() -> list[str]:
    return [n for n in await comfyui_client.get_loras() if "qwen" in n.lower()]


def _validate_loras(loras: list[LoRAConfig], available: list[str]) -> None:
    missing = [l.name for l in loras if l.name not in available]
    if missing:
        raise HTTPException(status_code=400, detail=f"Qwen-Image LoRA를 찾을 수 없습니다: {missing} (사용 가능: {available})")


@router.get("/qwen_models")
async def qwen_models():
    if not await comfyui_client.is_available():
        raise HTTPException(status_code=503, detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.")
    unets = await comfyui_client.get_gguf_unets()
    t2i_models = [n for n in unets if "qwen" in n.lower() and "edit" not in n.lower()]
    edit_models = [n for n in unets if "qwen" in n.lower() and "edit" in n.lower()]
    encoders = [n for n in await comfyui_client.get_text_encoders() if "qwen" in n.lower()]
    vaes = [n for n in await comfyui_client.get_vaes() if "qwen" in n.lower()]
    return {
        "t2i_available": bool(t2i_models and encoders and vaes),
        "edit_available": bool(edit_models and encoders and vaes),
        "loras": await _qwen_loras(),
    }


@router.post("/qwen_generate", response_model=GenerateResponse)
async def qwen_generate(req: QwenGenerateParams):
    if not await comfyui_client.is_available():
        raise HTTPException(
            status_code=503,
            detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.",
        )

    _validate_loras(req.loras, await _qwen_loras())
    unet, encoder, vae = await _pick_models(edit=False)
    log_generation_request(
        "/api/qwen_generate",
        req,
        model=unet,
        text_encoder=encoder,
        vae=vae,
    )
    workflow, seed = build_qwen_txt2img_workflow(req, unet, encoder, vae)

    generation_id = db.create_generation(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        model=unet,
        loras=[l.model_dump() for l in req.loras],
        seed=seed,
        steps=req.steps,
        cfg=req.cfg,
        width=req.width,
        height=req.height,
        sampler="euler",
        parent_image_id=req.parent_image_id,
        status="queued",
    )

    await queue_service.enqueue(generation_id, workflow, timeout=WAIT_TIMEOUT_SECONDS, free_before=True)

    return GenerateResponse(generation_id=generation_id, prompt_id="", status="queued")


@router.post("/qwen_edit", response_model=GenerateResponse)
async def qwen_edit(
    prompt: str = Form(...),
    seed: Optional[int] = Form(None),
    steps: int = Form(20),
    cfg: float = Form(4.0),
    loras: str = Form("[]"),
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
        with Image.open(io.BytesIO(content)) as im:
            source_size = im.size
    except Exception:
        raise HTTPException(status_code=400, detail="이미지를 읽을 수 없습니다.")

    try:
        lora_list = [LoRAConfig(**l) for l in json.loads(loras)]
    except (json.JSONDecodeError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="loras 필드는 [{name, weight}] 형식의 JSON이어야 합니다.")

    params = QwenEditParams(prompt=prompt, seed=seed, steps=steps, cfg=cfg, loras=lora_list, parent_image_id=parent_image_id)
    _validate_loras(params.loras, await _qwen_loras())
    unet, encoder, vae = await _pick_models(edit=True)

    upload_result = await comfyui_client.upload_image(filename, content)
    width, height = _fit_edit_size(source_size)
    log_generation_request(
        "/api/qwen_edit",
        params,
        model=unet,
        text_encoder=encoder,
        vae=vae,
        source_generation_id=source_generation_id,
        uploaded_file=filename if file is not None else None,
        width=width,
        height=height,
    )

    workflow, seed_used = build_qwen_edit_workflow(params, unet, encoder, vae, upload_result["name"], width, height)

    generation_id = db.create_generation(
        prompt=params.prompt,
        negative_prompt="",
        model=unet,
        loras=[l.model_dump() for l in params.loras],
        seed=seed_used,
        steps=params.steps,
        cfg=params.cfg,
        width=width,
        height=height,
        sampler="euler",
        parent_image_id=params.parent_image_id,
        status="queued",
    )

    await queue_service.enqueue(generation_id, workflow, timeout=WAIT_TIMEOUT_SECONDS, free_before=True)

    return GenerateResponse(generation_id=generation_id, prompt_id="", status="queued")
