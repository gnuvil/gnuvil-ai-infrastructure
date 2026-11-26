from fastapi import APIRouter, HTTPException

from app.core.comfyui_client import comfyui_client
from app.models.schemas import GenerateRequest, GenerateResponse
from app.services.workflow_service import build_txt2img_workflow
from app.services import db, queue_service
from app.services.request_logging import log_generation_request

router = APIRouter()

NOOBAI_VPRED_DEFAULTS = {
    "steps": 28,
    "cfg": 5.0,
    "sampler": "euler",
    "sampling": "v_prediction",
    "rescale_betas_zero_snr": True,
    "width": 832,
    "height": 1216,
}
NOOBAI_PROMPT_PREFIX = "masterpiece, best quality, newest, absurdres, highres, safe,"
NOOBAI_NEGATIVE_PROMPT = (
    "nsfw, worst quality, old, early, low quality, lowres, signature, username, logo, bad hands, "
    "mutated hands, mammal, anthro, furry, ambiguous form, feral, semi-anthro"
)


def apply_noobai_vpred_defaults(req: GenerateRequest) -> None:
    """Apply the UI's NoobAI v-pred preset only where the caller omitted a value."""
    checkpoint = req.checkpoint.lower().replace("_", "").replace("-", "")
    if "noobai" not in checkpoint or "vpred" not in checkpoint:
        return

    supplied = req.model_fields_set
    for field, value in NOOBAI_VPRED_DEFAULTS.items():
        if field not in supplied:
            setattr(req, field, value)

    if "negative_prompt" not in supplied:
        req.negative_prompt = NOOBAI_NEGATIVE_PROMPT

    prefix = req.prompt_prefix if "prompt_prefix" in supplied else NOOBAI_PROMPT_PREFIX
    prefix = (prefix or "").strip()
    if prefix and not req.prompt.lstrip().startswith(prefix):
        req.prompt = f"{prefix} {req.prompt.lstrip()}"


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    apply_noobai_vpred_defaults(req)
    log_generation_request("/api/generate", req)

    if not await comfyui_client.is_available():
        raise HTTPException(
            status_code=503,
            detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.",
        )

    checkpoints = await comfyui_client.get_checkpoints()
    if req.checkpoint not in checkpoints:
        raise HTTPException(
            status_code=400,
            detail=f"체크포인트 '{req.checkpoint}'를 찾을 수 없습니다. 사용 가능한 체크포인트: {checkpoints}",
        )

    if req.face_detailer:
        detectors = await comfyui_client.get_bbox_detectors()
        wanted = [m.strip() for m in (req.face_detailer_model or "").split(",") if m.strip()]
        if not wanted or any(m not in detectors for m in wanted):
            raise HTTPException(
                status_code=400,
                detail=f"얼굴 감지 모델 '{req.face_detailer_model}'을 찾을 수 없습니다. models/ultralytics/bbox 폴더에 파일을 추가하세요. 사용 가능한 목록: {detectors}",
            )

    if req.loras:
        available_loras = await comfyui_client.get_loras()
        missing = [l.name for l in req.loras if l.name not in available_loras]
        if missing:
            raise HTTPException(status_code=400, detail=f"LoRA를 찾을 수 없습니다: {missing}")

    workflow, seed = build_txt2img_workflow(req)

    generation_id = db.create_generation(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        model=req.checkpoint,
        loras=[l.model_dump() for l in req.loras],
        seed=seed,
        steps=req.steps,
        cfg=req.cfg,
        width=req.width,
        height=req.height,
        sampler=req.sampler,
        parent_image_id=req.parent_image_id,
        status="queued",
    )

    await queue_service.enqueue(generation_id, workflow)

    return GenerateResponse(generation_id=generation_id, prompt_id="", status="queued")
