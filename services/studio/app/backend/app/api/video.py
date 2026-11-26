import io
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel

from app.core.comfyui_client import comfyui_client
from app.core.config import OUTPUTS_DIR, UPLOADS_DIR
from app.models.schemas import GenerateResponse
from app.services import db, output_files, queue_service, video_extend
from app.services.generation_service import run_workflow_and_save
from app.services.request_logging import log_generation_request
from app.services.workflow_service import (
    apply_interpolation,
    build_interpolation_workflow,
    build_video_hq_workflow,
    build_video_workflow,
)

router = APIRouter()

# Limits measured on an RTX 3060 12GB with Wan 2.2 TI2V 5B: 480x704 x 49 frames peaked at ~11.6GB,
# so the pixel area and frame count are capped to what has actually been run.
MAX_PIXELS = 480 * 704
MAX_FRAMES = 49
HQ_MAX_FRAMES = 81  # 14B path (5s at 16fps); the real limit is confirmed by measurement, see HQ_LIMITS_NOTE
HQ_FPS = 16.0
MAX_SEGMENTS = 3  # each extra segment costs a full ~9 min generation and adds drift risk
SEGMENT_TIMEOUT_SECONDS = 1800
HQ_STAGE_NAMES = {"3": "샘플링 1/2 (고노이즈 모델)", "15": "샘플링 2/2 (저노이즈 모델)"}
MIN_FRAMES = 9
MIN_SIDE = 256
MAX_SIDE = 832
DEFAULT_NEGATIVE = (
    "blurry, low quality, static, distorted, deformed, extra fingers, worst quality, subtitles, watermark, "
    "cropped, cut off, out of frame, head out of frame, zoom in, close-up, body distortion, fast motion"
)
VIDEO_MODEL_HINT = "wan"
TEXT_ENCODER_HINT = "umt5"


def _snap(value: int, step: int) -> int:
    return max(step, round(value / step) * step)


def normalize_length(frames: int, max_frames: int = MAX_FRAMES) -> int:
    """Wan needs 4n+1 frames."""
    frames = max(MIN_FRAMES, min(max_frames, frames))
    return ((frames - 1) // 4) * 4 + 1


def _hq_models(unets: list[str], loras: list[str]) -> dict:
    """14B GGUF experts + Lightning LoRAs, found by name so nothing is hard-coded."""
    def first(names, *words):
        for n in names:
            low = n.lower().replace("-", "").replace("_", "")
            if all(w in low for w in words):
                return n
        return None

    high = first(unets, "i2v", "high")
    low = first(unets, "i2v", "low")
    high_lora = first(loras, "lightning", "high")
    low_lora = first(loras, "lightning", "low")
    return {
        "high": high,
        "low": low,
        "high_lora": high_lora,
        "low_lora": low_lora,
        "available": bool(high and low and high_lora and low_lora),
    }


def fit_size(width: Optional[int], height: Optional[int], source: Optional[tuple[int, int]]) -> tuple[int, int]:
    """Multiples of 32 within the measured pixel budget; follows the source image's aspect when no size is given."""
    if source and not (width and height):
        ratio = source[0] / source[1]
        h = (MAX_PIXELS / ratio) ** 0.5
        w = h * ratio
    else:
        w, h = width or 704, height or 480
    w, h = _snap(int(w), 32), _snap(int(h), 32)
    w, h = max(MIN_SIDE, min(MAX_SIDE, w)), max(MIN_SIDE, min(MAX_SIDE, h))
    while w * h > MAX_PIXELS:
        scale = (MAX_PIXELS / (w * h)) ** 0.5
        w, h = max(MIN_SIDE, _snap(int(w * scale), 32)), max(MIN_SIDE, _snap(int(h * scale) , 32))
        if w == MIN_SIDE and h == MIN_SIDE:
            break
    return w, h


def _pick(available: list[str], hint: str, wanted: Optional[str], label: str, folder: str) -> str:
    if wanted:
        if wanted not in available:
            raise HTTPException(status_code=400, detail=f"{label} '{wanted}'을(를) 찾을 수 없습니다. 사용 가능한 목록: {available}")
        return wanted
    for name in available:
        if hint in name.lower():
            return name
    raise HTTPException(
        status_code=400,
        detail=f"{label}이(가) 없습니다. {folder} 폴더에 파일을 넣고 ComfyUI를 재시작하세요. (현재 목록: {available})",
    )


def _pick_interpolation_model(available: list[str]) -> str:
    if not available:
        raise HTTPException(
            status_code=400,
            detail="프레임 보간 모델이 없습니다. models/frame_interpolation에 rife 또는 film 파일을 넣고 ComfyUI를 재시작하세요.",
        )
    for name in available:  # RIFE is about twice as fast as FILM at the same visible quality
        if "rife" in name.lower():
            return name
    return available[0]


@router.get("/video_models")
async def video_models():
    if not await comfyui_client.is_available():
        raise HTTPException(status_code=503, detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.")
    unets = [n for n in await comfyui_client.get_unets() if VIDEO_MODEL_HINT in n.lower()]
    encoders = [n for n in await comfyui_client.get_text_encoders() if TEXT_ENCODER_HINT in n.lower()]
    vaes = [n for n in await comfyui_client.get_vaes() if n.lower().startswith("wan")]
    hq = _hq_models(await comfyui_client.get_gguf_unets(), await comfyui_client.get_loras())
    return {
        "models": unets,
        "text_encoders": encoders,
        "vaes": vaes,
        "available": bool(unets and encoders and any("2.2" in v for v in vaes)),
        "hq_available": bool(hq["available"] and encoders and any("2.1" in v for v in vaes)),
        "interpolation_available": bool(await comfyui_client.get_interpolation_models()),
        "limits": {
            "max_pixels": MAX_PIXELS,
            "max_frames": MAX_FRAMES,
            "hq_max_frames": HQ_MAX_FRAMES,
            "min_frames": MIN_FRAMES,
        },
    }


async def create_video_job(
    prompt: str,
    negative_prompt: str = DEFAULT_NEGATIVE,
    seed: Optional[int] = None,
    steps: int = 20,
    cfg: float = 5.0,
    width: Optional[int] = None,
    height: Optional[int] = None,
    length: int = 49,
    fps: Optional[float] = None,
    quality: str = "auto",
    segments: int = 1,
    interpolate: int = 1,
    video_model: Optional[str] = None,
    text_encoder: Optional[str] = None,
    video_vae: Optional[str] = None,
    parent_image_id: Optional[int] = None,
    source_generation_id: Optional[int] = None,
    file: Optional[UploadFile] = None,
) -> GenerateResponse:
    """Image-to-video when a file or source_generation_id is given, text-to-video otherwise."""
    if not await comfyui_client.is_available():
        raise HTTPException(
            status_code=503,
            detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.",
        )
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="프롬프트가 비어 있습니다.")
    if not 1 <= steps <= 50:
        raise HTTPException(status_code=400, detail="steps는 1~50 사이여야 합니다.")
    if fps is not None and not 8 <= fps <= 30:
        raise HTTPException(status_code=400, detail="fps는 8~30 사이여야 합니다.")
    if quality not in ("auto", "fast", "high"):
        raise HTTPException(status_code=400, detail="quality는 auto, fast, high 중 하나여야 합니다.")

    has_image = file is not None or source_generation_id is not None
    hq = _hq_models(await comfyui_client.get_gguf_unets(), await comfyui_client.get_loras())
    if quality == "high" and not has_image:
        raise HTTPException(status_code=400, detail="고화질 모드는 시작 이미지가 필요합니다. 텍스트→영상은 빠른 모드만 지원합니다.")
    if quality == "high" and not hq["available"]:
        raise HTTPException(
            status_code=400,
            detail="고화질 모델(Wan 2.2 I2V 14B GGUF 고노이즈/저노이즈 + Lightning LoRA)이 없습니다. "
            "models/diffusion_models와 models/loras에 파일을 넣고 ComfyUI를 재시작하세요.",
        )
    use_hq = has_image and hq["available"] and quality in ("auto", "high")
    if interpolate not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="보간 배수는 1(끄기), 2, 3, 4 중 하나여야 합니다.")
    interp_model = None
    if interpolate > 1:
        interp_model = _pick_interpolation_model(await comfyui_client.get_interpolation_models())
    if not 1 <= segments <= MAX_SEGMENTS:
        raise HTTPException(status_code=400, detail=f"구간 수는 1~{MAX_SEGMENTS} 사이여야 합니다.")
    if segments > 1:
        if not use_hq:
            raise HTTPException(status_code=400, detail="여러 구간 이어 붙이기는 고화질(14B) 이미지→영상에서만 지원합니다.")
        try:
            video_extend.require_ffmpeg()
        except video_extend.FfmpegMissingError as e:
            raise HTTPException(status_code=400, detail=str(e))

    encoder = _pick(await comfyui_client.get_text_encoders(), TEXT_ENCODER_HINT, text_encoder, "텍스트 인코더", "models/text_encoders")
    vae_names = [n for n in await comfyui_client.get_vaes() if n.lower().startswith("wan")]
    vae = _pick(vae_names, "2.1" if use_hq else "2.2", video_vae, "영상 VAE", "models/vae")
    unet = hq["high"] if use_hq else _pick(await comfyui_client.get_unets(), VIDEO_MODEL_HINT, video_model, "영상 모델", "models/diffusion_models")

    content: Optional[bytes] = None
    filename = "video_source.png"
    if file is not None:
        content = await file.read()
        filename = file.filename or filename
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        (UPLOADS_DIR / filename).write_bytes(content)
    elif source_generation_id is not None:
        source = db.get_generation(source_generation_id)
        if source is None or not source["image_path"]:
            raise HTTPException(status_code=404, detail="원본 생성 기록을 찾을 수 없습니다.")
        if source["media_type"] == "video":
            raise HTTPException(status_code=400, detail="영상은 시작 이미지로 쓸 수 없습니다. 이미지를 선택하세요.")
        source_path = OUTPUTS_DIR / Path(source["image_path"]).name
        if not source_path.exists():
            raise HTTPException(status_code=404, detail="원본 이미지 파일을 찾을 수 없습니다.")
        content = source_path.read_bytes()
        filename = source_path.name
        if parent_image_id is None:
            parent_image_id = source_generation_id

    source_size = None
    image_name = None
    if content is not None:
        try:
            with Image.open(io.BytesIO(content)) as im:
                source_size = im.size
        except Exception:
            raise HTTPException(status_code=400, detail="시작 이미지를 읽을 수 없습니다.")
        upload_result = await comfyui_client.upload_image(filename, content)
        image_name = upload_result["name"]

    w, h = fit_size(width, height, source_size)
    frames = normalize_length(length, HQ_MAX_FRAMES if use_hq else MAX_FRAMES)

    if use_hq:
        fps = fps or HQ_FPS
        steps, cfg = 4, 1.0  # fixed by the 4-step Lightning LoRAs
        workflow, seed_used = build_video_hq_workflow(
            prompt=prompt,
            negative_prompt=negative_prompt,
            high_model=hq["high"],
            low_model=hq["low"],
            high_lora=hq["high_lora"],
            low_lora=hq["low_lora"],
            text_encoder=encoder,
            vae=vae,
            width=w,
            height=h,
            length=frames,
            seed=seed,
            fps=fps,
            image_name=image_name,
        )
    else:
        fps = fps or 24.0
        workflow, seed_used = build_video_workflow(
            prompt=prompt,
            negative_prompt=negative_prompt,
            unet=unet,
            text_encoder=encoder,
            vae=vae,
            width=w,
            height=h,
            length=frames,
            steps=steps,
            cfg=cfg,
            seed=seed,
            fps=fps,
            image_name=image_name,
        )

    workflow = apply_interpolation(workflow, interpolate, interp_model)
    if interpolate > 1:
        frames = (frames - 1) * interpolate + 1
        fps = fps * interpolate
    post_process = None
    total_frames = frames
    if segments > 1:
        total_frames = frames * segments - (segments - 1)

        seg_length = normalize_length(length, HQ_MAX_FRAMES)  # frames asked of the model (before interpolation)
        seg_fps = fps / interpolate if interpolate > 1 else fps

        async def post_process(first_clip: Path, _seg=segments, _seed=seed_used):
            clips = [first_clip]
            for i in range(1, _seg):
                await queue_service.publish_stage(
                    generation_id, f"구간 {i + 1}/{_seg} 준비 중 (이전 구간 마지막 프레임 추출)", segment=[i + 1, _seg]
                )
                last = await video_extend.extract_last_frame(clips[-1])
                up = await comfyui_client.upload_image(f"seg{i}_{first_clip.stem}.png", last)
                seg_workflow, _ = build_video_hq_workflow(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    high_model=hq["high"],
                    low_model=hq["low"],
                    high_lora=hq["high_lora"],
                    low_lora=hq["low_lora"],
                    text_encoder=encoder,
                    vae=vae,
                    width=w,
                    height=h,
                    length=seg_length,
                    seed=(_seed + i) % (2**32),
                    fps=seg_fps,
                    image_name=up["name"],
                )
                seg_workflow = apply_interpolation(seg_workflow, interpolate, interp_model)
                seg_job = queue_service.GenerationJob(
                    generation_id,
                    seg_workflow,
                    timeout=queue_service.VIDEO_WAIT_TIMEOUT_SECONDS,
                    dimensions=(w, h),
                    progress_nodes=("15", "3"),
                    stage_names=HQ_STAGE_NAMES,
                    segment=(i + 1, _seg),
                )
                seg_path, _, _ = await queue_service.run_job(seg_job)
                clips.append(seg_path)
            await queue_service.publish_stage(generation_id, "구간을 하나로 합치는 중")
            return await video_extend.join_segments(clips, first_clip.stem)

    generation_id = db.create_generation(
        prompt=prompt,
        negative_prompt=negative_prompt,
        model=unet,
        loras=[],
        seed=seed_used,
        steps=steps,
        cfg=cfg,
        width=w,
        height=h,
        sampler="euler" if use_hq else "uni_pc",
        parent_image_id=parent_image_id,
        status="queued",
        media_type="video",
        frames=total_frames,
    )

    await queue_service.enqueue(
        generation_id,
        workflow,
        post_process,
        timeout=queue_service.VIDEO_WAIT_TIMEOUT_SECONDS,
        dimensions=(w, h),
        free_before=True,
        progress_nodes=("15", "3") if use_hq else ("3",),
        stage_names=HQ_STAGE_NAMES if use_hq else {"3": "샘플링"},
        segment=(1, segments) if segments > 1 else None,
    )
    return GenerateResponse(generation_id=generation_id, prompt_id="", status="queued")


@router.post("/video_generate", response_model=GenerateResponse)
async def video_generate(
    prompt: str = Form(...),
    negative_prompt: str = Form(DEFAULT_NEGATIVE),
    seed: Optional[int] = Form(None),
    steps: int = Form(20),
    cfg: float = Form(5.0),
    width: Optional[int] = Form(None),
    height: Optional[int] = Form(None),
    length: int = Form(49),
    fps: Optional[float] = Form(None),
    quality: str = Form("auto"),
    segments: int = Form(1),
    interpolate: int = Form(1),
    video_model: Optional[str] = Form(None),
    text_encoder: Optional[str] = Form(None),
    video_vae: Optional[str] = Form(None),
    parent_image_id: Optional[int] = Form(None),
    source_generation_id: Optional[int] = Form(None),
    file: Optional[UploadFile] = None,
):
    """Image-to-video when a file or source_generation_id is given, text-to-video otherwise."""
    log_generation_request(
        "/api/video_generate",
        {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "width": width,
            "height": height,
            "length": length,
            "fps": fps,
            "quality": quality,
            "segments": segments,
            "interpolate": interpolate,
            "video_model": video_model,
            "text_encoder": text_encoder,
            "video_vae": video_vae,
            "parent_image_id": parent_image_id,
            "source_generation_id": source_generation_id,
            "uploaded_file": file.filename if file else None,
        },
    )
    return await create_video_job(
        prompt, negative_prompt, seed, steps, cfg, width, height, length, fps, quality, segments, interpolate,
        video_model, text_encoder, video_vae, parent_image_id, source_generation_id, file,
    )


class InterpolateRequest(BaseModel):
    generation_id: int
    multiplier: int = 2


@router.post("/video_interpolate", response_model=GenerateResponse)
async def video_interpolate(req: InterpolateRequest):
    """Smooth an already generated video by inserting frames (RIFE/FILM)."""
    if not await comfyui_client.is_available():
        raise HTTPException(status_code=503, detail="ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요.")
    if req.multiplier not in (2, 3, 4):
        raise HTTPException(status_code=400, detail="배수는 2, 3, 4 중 하나여야 합니다.")
    row = db.get_generation(req.generation_id)
    if row is None or not row["image_path"] or row["media_type"] != "video":
        raise HTTPException(status_code=404, detail="보간할 영상 기록을 찾을 수 없습니다.")
    src = output_files.find_result(row["image_path"])
    if src is None:
        raise HTTPException(status_code=404, detail="영상 파일을 찾을 수 없습니다.")

    model = _pick_interpolation_model(await comfyui_client.get_interpolation_models())
    # the source clip's frame rate: frames / duration is not stored, so read it from the file
    fps = await _video_fps(src)
    upload = await comfyui_client.upload_image(f"interp_{src.name}", src.read_bytes())

    workflow = build_interpolation_workflow(upload["name"], model, req.multiplier, fps)
    frames = (row["frames"] - 1) * req.multiplier + 1 if row["frames"] else 0
    generation_id = db.create_generation(
        prompt=f"[보간 x{req.multiplier}] {row['prompt']}",
        negative_prompt=row["negative_prompt"],
        model=model,
        loras=[],
        seed=row["seed"],
        steps=0,
        cfg=0,
        width=row["width"],
        height=row["height"],
        sampler="interpolation",
        parent_image_id=req.generation_id,
        status="queued",
        media_type="video",
        frames=frames,
    )
    await queue_service.enqueue(
        generation_id,
        workflow,
        timeout=queue_service.VIDEO_WAIT_TIMEOUT_SECONDS,
        dimensions=(row["width"], row["height"]),
        free_before=True,
        progress_nodes=("4",),
    )
    return GenerateResponse(generation_id=generation_id, prompt_id="", status="queued")


async def _video_fps(path: Path) -> float:
    import asyncio
    import shutil

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 16.0
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    try:
        num, den = out.decode().strip().split("/")
        return float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return 16.0
