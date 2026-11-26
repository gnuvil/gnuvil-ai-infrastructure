import asyncio
import json
import logging
import os
import time

import aiohttp

from app.core.comfyui_client import COMFYUI_OFF_MESSAGE, comfyui_client, ComfyUIUnavailableError
from app.core.config import COMFYUI_WS_URL, OUTPUTS_DIR
from app.services import db
from app.services.generation_service import get_image_size
from app.services.vram_manager import gpu_lock

logger = logging.getLogger(__name__)

KSAMPLER_NODE_ID = "3"
WAIT_FOR_RESULT_TIMEOUT_SECONDS = 300
VIDEO_WAIT_TIMEOUT_SECONDS = 1800
VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv")

SAMPLER_CLASSES = ("KSampler", "KSamplerAdvanced")
PROGRESS_CLASSES = SAMPLER_CLASSES + ("FrameInterpolate",)
# what the user sees while a ComfyUI node runs; unknown nodes keep the previous label
STAGE_LABELS = {
    "CheckpointLoaderSimple": "모델 불러오는 중",
    "UNETLoader": "모델 불러오는 중",
    "UnetLoaderGGUF": "모델 불러오는 중",
    "LoraLoader": "LoRA 적용 중",
    "LoraLoaderModelOnly": "LoRA 적용 중",
    "CLIPLoader": "텍스트 인코더 불러오는 중",
    "CLIPTextEncode": "프롬프트 처리 중",
    "VAELoader": "VAE 불러오는 중",
    "ModelSamplingDiscrete": "모델 설정 중",
    "ModelSamplingSD3": "모델 설정 중",
    "EmptyLatentImage": "생성 준비 중",
    "LoadImage": "입력 이미지 준비 중",
    "LoadImageMask": "마스크 준비 중",
    "VAEEncode": "입력 이미지 인코딩 중",
    "VAEEncodeForInpaint": "마스크 영역 준비 중",
    "SetLatentNoiseMask": "마스크 영역 준비 중",
    "ControlNetLoader": "ControlNet 준비 중",
    "ControlNetApplyAdvanced": "ControlNet 적용 중",
    "DWPreprocessor": "포즈 추출 중",
    "Canny": "윤곽선 추출 중",
    "IPAdapterAdvanced": "IP-Adapter 적용 중",
    "WanImageToVideo": "영상 시작 조건 준비 중",
    "Wan22ImageToVideoLatent": "영상 시작 조건 준비 중",
    "LatentUpscaleBy": "Hires 확대 중",
    "VAEDecode": "이미지로 복원(디코딩) 중",
    "FaceDetailer": "얼굴·손 보정 중",
    "UltralyticsDetectorProvider": "얼굴·손 감지 준비 중",
    "ImageCompositeMasked": "원본과 합성 중",
    "LoadVideo": "영상 불러오는 중",
    "GetVideoComponents": "프레임 추출 중",
    "FrameInterpolationModelLoader": "보간 모델 불러오는 중",
    "FrameInterpolate": "프레임 보간 중",
    "CreateVideo": "영상 인코딩 중",
    "SaveVideo": "영상 저장 중",
    "SaveImage": "이미지 저장 중",
}


def _expected_steps(workflow: dict) -> int:
    """Rough total sampling steps of a workflow, used for the overall sampling percentage."""
    total = 0
    for node in workflow.values():
        c = node.get("class_type")
        inp = node.get("inputs", {})
        try:
            if c == "KSampler":
                total += int(inp.get("steps", 0))
            elif c == "KSamplerAdvanced":
                steps = int(inp.get("steps", 0))
                end = min(int(inp.get("end_at_step", steps)), steps)
                total += max(0, end - int(inp.get("start_at_step", 0)))
        except (TypeError, ValueError):
            continue
    return total


class GenerationJob:
    def __init__(
        self,
        generation_id: int,
        workflow: dict,
        post_process=None,
        timeout: float = WAIT_FOR_RESULT_TIMEOUT_SECONDS,
        dimensions: "tuple[int, int] | None" = None,
        free_before: bool = False,
        progress_nodes: "tuple[str, ...]" = (KSAMPLER_NODE_ID,),
        stage_names: "dict[str, str] | None" = None,
        segment: "tuple[int, int] | None" = None,
    ):
        self.generation_id = generation_id
        self.workflow = workflow
        self.timeout = timeout  # max seconds without a ComfyUI message
        self.dimensions = dimensions  # (w, h) for results PIL cannot read, i.e. videos
        self.stage_names = stage_names or {}  # node id -> label, e.g. the two video samplers
        self.segment = segment  # (index, total) when this job is one part of a chained video
        self.progress_nodes = progress_nodes  # nodes whose sampling progress is relayed, first match wins
        self.free_before = free_before  # release ComfyUI's cached models first (video needs the whole GPU)
        # async callable(Path) -> Path, run after the workflow (e.g. paste a crop back into the original)
        self.post_process = post_process


_job_queue: "asyncio.Queue[GenerationJob]" = asyncio.Queue()
_queue_order: list[int] = []
_current_generation_id: int | None = None
_cancel_requested: set[int] = set()
_subscribers: dict[int, list[asyncio.Queue]] = {}
_worker_task: asyncio.Task | None = None
_idle_task: asyncio.Task | None = None

# Seconds of an empty queue after which ComfyUI's models are unloaded from the GPU, so other GPU programs
# (Premiere, games, the browser) are not starved of VRAM. 0 disables it. The next job pays a model reload.
IDLE_FREE_SECONDS = float(os.environ.get("LOCAL_AI_IDLE_FREE_SECONDS", "20"))
_job_started: dict[int, float] = {}


def get_queue_snapshot() -> dict:
    return {"current": _current_generation_id, "queued": list(_queue_order)}


async def enqueue(generation_id: int, workflow: dict, post_process=None, **job_options):
    if _idle_task and not _idle_task.done():
        _idle_task.cancel()
    _queue_order.append(generation_id)
    await _job_queue.put(GenerationJob(generation_id, workflow, post_process, **job_options))


def subscribe(generation_id: int) -> "asyncio.Queue":
    q: "asyncio.Queue" = asyncio.Queue()
    _subscribers.setdefault(generation_id, []).append(q)
    return q


def unsubscribe(generation_id: int, q: "asyncio.Queue"):
    subs = _subscribers.get(generation_id)
    if subs and q in subs:
        subs.remove(q)
        if not subs:
            _subscribers.pop(generation_id, None)


async def _publish(generation_id: int, message: dict):
    for q in _subscribers.get(generation_id, []):
        await q.put(message)


def queue_position(generation_id: int) -> int | None:
    """0 = running/next; N = N jobs ahead."""
    return _queue_order.index(generation_id) if generation_id in _queue_order else None


async def publish_stage(generation_id: int, stage: str, **extra):
    await _publish(generation_id, {"type": "stage", "stage": stage, "elapsed": _elapsed(generation_id), **extra})


def _elapsed(generation_id: int) -> float:
    started = _job_started.get(generation_id)
    return round(time.monotonic() - started, 1) if started else 0.0


def request_cancel(generation_id: int) -> bool:
    if _current_generation_id == generation_id or generation_id in _queue_order:
        _cancel_requested.add(generation_id)
        return True
    return False


async def _run_job_with_progress(job: GenerationJob) -> "tuple[object, int, int]":
    client_id = comfyui_client.new_client_id()
    prompt_id: str | None = None

    ws_url = f"{COMFYUI_WS_URL}?clientId={client_id}"
    classes = {nid: n.get("class_type", "") for nid, n in job.workflow.items()}
    total_steps = _expected_steps(job.workflow)
    finished_steps = 0
    counted_nodes: set[str] = set()
    sampling_started: float | None = None
    stage = "준비 중"
    sampler_order = [nid for nid, c in classes.items() if c in SAMPLER_CLASSES]

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url, timeout=job.timeout) as ws:
            prompt_id = await comfyui_client.queue_prompt(job.workflow, client_id)
            await _publish(job.generation_id, {"type": "stage", "stage": stage, "elapsed": _elapsed(job.generation_id),
                                                "segment": list(job.segment) if job.segment else None})
            while True:
                if job.generation_id in _cancel_requested:
                    await comfyui_client.interrupt()

                msg = await asyncio.wait_for(ws.receive(), timeout=job.timeout)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                mtype = data.get("type")
                payload = data.get("data", {})

                if payload.get("prompt_id") not in (None, prompt_id):
                    continue

                if mtype == "executing" and payload.get("node"):
                    nid = str(payload["node"])
                    label = job.stage_names.get(nid) or STAGE_LABELS.get(classes.get(nid, ""))
                    if classes.get(nid) in SAMPLER_CLASSES:
                        label = job.stage_names.get(nid) or "샘플링"
                    if label and label != stage:
                        stage = label
                        await _publish(
                            job.generation_id,
                            {"type": "stage", "stage": stage, "elapsed": _elapsed(job.generation_id),
                             "segment": list(job.segment) if job.segment else None},
                        )
                elif mtype == "progress_state":
                    nodes = payload.get("nodes", {})
                    running = [n for n, st in nodes.items() if classes.get(n) in PROGRESS_CLASSES and st.get("state") == "running"]
                    node_id = running[-1] if running else next((n for n in job.progress_nodes if n in nodes), None)
                    node_state = nodes.get(node_id) if node_id else None
                    # a sampler that has finished counts towards the overall total
                    for nid, st in nodes.items():
                        if classes.get(nid) in SAMPLER_CLASSES and nid not in counted_nodes and st.get("max") and st.get("value", 0) >= st.get("max", 0):
                            counted_nodes.add(nid)
                            finished_steps += int(st.get("max", 0))
                    if node_state:
                        value, maximum = node_state.get("value", 0), node_state.get("max", 0)
                        now = time.monotonic()
                        if sampling_started is None and value > 0:
                            sampling_started = now
                        done = min(total_steps, finished_steps + (0 if node_id in counted_nodes else value))
                        eta = None
                        if sampling_started and done >= 2 and total_steps > done:
                            eta = round((now - sampling_started) / done * (total_steps - done), 1)
                        idx = sampler_order.index(node_id) + 1 if node_id in sampler_order else None
                        await _publish(
                            job.generation_id,
                            {
                                "type": "progress",
                                "stage": stage,
                                "value": value,
                                "max": maximum,
                                "done": done,
                                "total": total_steps,
                                "sampler": [idx, len(sampler_order)] if idx else None,
                                "elapsed": _elapsed(job.generation_id),
                                "eta": eta,
                                "segment": list(job.segment) if job.segment else None,
                            },
                        )
                elif mtype == "execution_interrupted":
                    raise InterruptedError("Generation cancelled")
                elif mtype == "execution_error":
                    raise ComfyUIUnavailableError(payload.get("exception_message", "ComfyUI execution error"))
                elif mtype == "execution_success":
                    break

    history = await comfyui_client.get_history(prompt_id)
    entry = history.get(prompt_id, {})
    outputs = entry.get("outputs", {})
    # ComfyUI also lists previews of *input* files (e.g. LoadVideo); the result is the saved "output" one
    candidates = [o["images"][0] for o in outputs.values() if o.get("images")]
    candidates.sort(key=lambda i: 0 if i.get("type") == "output" else 1)
    if candidates:
        image_info = candidates[0]
        image_bytes = await comfyui_client.get_image_bytes(
            image_info["filename"], image_info["subfolder"], image_info["type"]
        )
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUTS_DIR / image_info["filename"]
        out_path.write_bytes(image_bytes)
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise ComfyUIUnavailableError(f"결과 파일을 저장하지 못했습니다: {out_path.name}")
        if out_path.suffix.lower() in VIDEO_SUFFIXES:
            width, height = job.dimensions or (0, 0)
        else:
            width, height = get_image_size(out_path)
        return out_path, width, height

    raise ComfyUIUnavailableError("ComfyUI did not return an image")


async def run_job(job: GenerationJob):
    """Runs one workflow with the same progress/cancel handling as a queued job (used for chained video segments)."""
    return await _run_job_with_progress(job)


async def _free_vram_when_idle():
    """Unloads ComfyUI's models once the queue has stayed empty for IDLE_FREE_SECONDS."""
    try:
        await asyncio.sleep(IDLE_FREE_SECONDS)
        if _current_generation_id is None and _job_queue.empty() and not gpu_lock.locked():
            if await comfyui_client.free_memory():
                logger.info("queue idle for %ss: unloaded ComfyUI models to free VRAM", IDLE_FREE_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("idle VRAM release failed")


def _schedule_idle_free():
    global _idle_task
    if IDLE_FREE_SECONDS <= 0:
        return
    if _idle_task and not _idle_task.done():
        _idle_task.cancel()
    _idle_task = asyncio.create_task(_free_vram_when_idle())


async def _worker_loop():
    global _current_generation_id
    while True:
        job = await _job_queue.get()

        if job.generation_id in _cancel_requested:
            _cancel_requested.discard(job.generation_id)
            db.update_generation_status(job.generation_id, "cancelled")
            await _publish(job.generation_id, {"type": "cancelled"})
            if job.generation_id in _queue_order:
                _queue_order.remove(job.generation_id)
            continue

        _current_generation_id = job.generation_id
        _job_started[job.generation_id] = time.monotonic()
        db.update_generation_status(job.generation_id, "running")
        await _publish(job.generation_id, {"type": "status", "status": "running"})
        for waiting_id in list(_queue_order):
            if waiting_id != job.generation_id:
                await _publish(waiting_id, {"type": "queue", "ahead": _queue_order.index(waiting_id)})

        try:
            # ComfyUI is a switchable GPU service on the server: it may have been stopped while this job waited.
            if not await comfyui_client.is_available():
                raise ComfyUIUnavailableError(COMFYUI_OFF_MESSAGE)
            async with gpu_lock:
                if job.free_before:
                    await comfyui_client.free_memory()
                image_path, width, height = await _run_job_with_progress(job)
                if job.post_process is not None:
                    image_path = await job.post_process(image_path)
                    if image_path.suffix.lower() not in VIDEO_SUFFIXES:
                        width, height = get_image_size(image_path)
            db.update_generation_status(job.generation_id, "completed", image_path=f"/outputs/{image_path.name}")
            db.update_generation_dimensions(job.generation_id, width, height)
            await _publish(
                job.generation_id,
                {"type": "completed", "image_path": f"/outputs/{image_path.name}"},
            )
        except InterruptedError:
            db.update_generation_status(job.generation_id, "cancelled")
            await _publish(job.generation_id, {"type": "cancelled"})
        except Exception as e:
            logger.exception("Generation %s failed", job.generation_id)
            db.update_generation_status(job.generation_id, "failed")
            await _publish(job.generation_id, {"type": "failed", "error": str(e)})
        finally:
            _cancel_requested.discard(job.generation_id)
            _job_started.pop(job.generation_id, None)
            if job.generation_id in _queue_order:
                _queue_order.remove(job.generation_id)
            _current_generation_id = None
            if _job_queue.empty():
                _schedule_idle_free()


def start_worker():
    global _worker_task
    if _worker_task is None:
        _worker_task = asyncio.create_task(_worker_loop())
