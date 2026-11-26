import json
import random
import copy

from app.core.config import WORKFLOWS_DIR
from app.models.schemas import (
    GenerateRequest,
    Img2ImgParams,
    InpaintParams,
    ControlNetGenerateParams,
    IPAdapterGenerateParams,
    LoRAConfig,
    QwenGenerateParams,
    QwenEditParams,
)


def load_workflow(name: str) -> dict:
    path = WORKFLOWS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Workflow template not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_loras_and_vae(workflow: dict, loras: list[LoRAConfig], vae: str | None) -> dict:
    """Chains LoraLoader nodes after the checkpoint and optionally overrides the VAE.

    Rewrites every node input that still points at the checkpoint's raw
    MODEL/CLIP/VAE outputs (node "4") to point at the last injected node instead,
    so this works unmodified across the txt2img/img2img/inpaint templates.
    """
    model_ref = ["4", 0]
    clip_ref = ["4", 1]
    vae_ref = ["4", 2]

    original_node_ids = list(workflow.keys())
    next_id = max(int(k) for k in workflow.keys()) + 1

    for lora in loras:
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": model_ref,
                "clip": clip_ref,
                "lora_name": lora.name,
                "strength_model": lora.weight,
                "strength_clip": lora.weight,
            },
        }
        model_ref = [node_id, 0]
        clip_ref = [node_id, 1]

    if vae:
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {"class_type": "VAELoader", "inputs": {"vae_name": vae}}
        vae_ref = [node_id, 0]

    for node_id in original_node_ids:
        inputs = workflow[node_id].get("inputs", {})
        if inputs.get("model") == ["4", 0]:
            inputs["model"] = model_ref
        if inputs.get("clip") == ["4", 1]:
            inputs["clip"] = clip_ref
        if inputs.get("vae") == ["4", 2]:
            inputs["vae"] = vae_ref

    return workflow


def apply_hires_fix(workflow: dict, enabled: bool, scale: float, denoise: float) -> dict:
    """Appends a second, higher-resolution KSampler pass fed by a latent upscale of the
    first pass's result (classic "hires fix"). No extra model file is required.

    Reads node "3" (KSampler) for its current model/positive/negative refs, so this must
    run after apply_loras_and_vae so it picks up the final (possibly LoRA-routed) refs.
    Node "8" (VAEDecode) is repointed at the new pass's output.
    """
    if not enabled:
        return workflow

    base_inputs = workflow["3"]["inputs"]
    next_id = max(int(k) for k in workflow.keys()) + 1
    upscale_id = str(next_id)
    resample_id = str(next_id + 1)

    workflow[upscale_id] = {
        "class_type": "LatentUpscaleBy",
        "inputs": {"samples": ["3", 0], "upscale_method": "nearest-exact", "scale_by": scale},
    }
    workflow[resample_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": base_inputs["seed"],
            "steps": base_inputs["steps"],
            "cfg": base_inputs["cfg"],
            "sampler_name": base_inputs["sampler_name"],
            "scheduler": base_inputs["scheduler"],
            "denoise": denoise,
            "model": base_inputs["model"],
            "positive": base_inputs["positive"],
            "negative": base_inputs["negative"],
            "latent_image": [upscale_id, 0],
        },
    }
    workflow["8"]["inputs"]["samples"] = [resample_id, 0]

    return workflow


def apply_ipadapter(
    workflow: dict,
    enabled: bool,
    reference_image_name: str,
    ipadapter_model: str,
    clip_vision_model: str,
    weight: float,
    weight_type: str,
    start_at: float,
    end_at: float,
) -> dict:
    """Inserts an IPAdapterAdvanced node between the LoRA chain and ModelSamplingDiscrete
    (node "10"), so the reference image's style/subject conditions the whole generation.

    Must run after apply_loras_and_vae so it picks up the final MODEL ref.
    """
    if not enabled:
        return workflow

    next_id = max(int(k) for k in workflow.keys()) + 1
    image_id = str(next_id)
    clip_vision_id = str(next_id + 1)
    loader_id = str(next_id + 2)
    apply_id = str(next_id + 3)

    current_model_ref = workflow["10"]["inputs"]["model"]

    workflow[image_id] = {"class_type": "LoadImage", "inputs": {"image": reference_image_name}}
    workflow[clip_vision_id] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": clip_vision_model}}
    workflow[loader_id] = {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": ipadapter_model}}
    workflow[apply_id] = {
        "class_type": "IPAdapterAdvanced",
        "inputs": {
            "model": current_model_ref,
            "ipadapter": [loader_id, 0],
            "image": [image_id, 0],
            "weight": weight,
            "weight_type": weight_type,
            "combine_embeds": "concat",
            "start_at": start_at,
            "end_at": end_at,
            "embeds_scaling": "V only",
            "clip_vision": [clip_vision_id, 0],
        },
    }
    workflow["10"]["inputs"]["model"] = [apply_id, 0]

    return workflow


def apply_face_detailer(workflow: dict, enabled: bool, bbox_model: str | None, denoise: float) -> dict:
    """Chains one UltralyticsDetectorProvider + FaceDetailer pass per detector model.

    `bbox_model` may hold several comma-separated model names (e.g. face then hand); each
    pass re-samples only the regions its detector finds and feeds the next pass. The last
    pass is wired into SaveImage (node "9").

    Reads node "3" (KSampler) for model/seed/steps/cfg/sampler/scheduler, node "6" for
    clip/positive, node "7" for negative, and node "8" for vae, so this must run after
    apply_loras_and_vae / apply_ipadapter. The first pass reads node "8" (VAEDecode)'s
    output, so it also picks up whatever apply_hires_fix already produced there.
    """
    models = [m.strip() for m in (bbox_model or "").split(",") if m.strip()]
    if not enabled or not models:
        return workflow

    base = workflow["3"]["inputs"]
    image_ref = ["8", 0]

    for model_name in models:
        next_id = max(int(k) for k in workflow.keys()) + 1
        detector_id = str(next_id)
        detailer_id = str(next_id + 1)

        workflow[detector_id] = {"class_type": "UltralyticsDetectorProvider", "inputs": {"model_name": model_name}}
        workflow[detailer_id] = {
            "class_type": "FaceDetailer",
            "inputs": {
                "image": image_ref,
                "model": base["model"],
                "clip": workflow["6"]["inputs"]["clip"],
                "vae": workflow["8"]["inputs"]["vae"],
                "guide_size": 512,
                "guide_size_for": True,
                "max_size": 1024,
                "seed": base["seed"],
                "steps": base["steps"],
                "cfg": base["cfg"],
                "sampler_name": base["sampler_name"],
                "scheduler": base["scheduler"],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "denoise": denoise,
                "feather": 5,
                "noise_mask": True,
                "force_inpaint": True,
                "bbox_threshold": 0.5,
                "bbox_dilation": 10,
                "bbox_crop_factor": 3.0,
                "sam_detection_hint": "center-1",
                "sam_dilation": 0,
                "sam_threshold": 0.93,
                "sam_bbox_expansion": 0,
                "sam_mask_hint_threshold": 0.7,
                "sam_mask_hint_use_negative": "False",
                "drop_size": 10,
                "bbox_detector": [detector_id, 0],
                "wildcard": "",
                "cycle": 1,
            },
        }
        image_ref = [detailer_id, 0]

    workflow["9"]["inputs"]["images"] = image_ref
    return workflow


def build_txt2img_workflow(req: GenerateRequest) -> tuple[dict, int]:
    workflow = copy.deepcopy(load_workflow("txt2img"))
    seed = req.seed if req.seed is not None else random.randint(0, 2**32 - 1)

    workflow["4"]["inputs"]["ckpt_name"] = req.checkpoint
    workflow["10"]["inputs"]["sampling"] = req.sampling
    workflow["10"]["inputs"]["zsnr"] = bool(req.rescale_betas_zero_snr)
    workflow["5"]["inputs"]["width"] = req.width
    workflow["5"]["inputs"]["height"] = req.height
    workflow["6"]["inputs"]["text"] = req.prompt
    workflow["7"]["inputs"]["text"] = req.negative_prompt
    workflow["3"]["inputs"]["seed"] = seed
    workflow["3"]["inputs"]["steps"] = req.steps
    workflow["3"]["inputs"]["cfg"] = req.cfg
    workflow["3"]["inputs"]["sampler_name"] = req.sampler

    apply_loras_and_vae(workflow, req.loras, req.vae)
    apply_hires_fix(workflow, req.hires_fix, req.hires_scale, req.hires_denoise)
    apply_face_detailer(workflow, req.face_detailer, req.face_detailer_model, req.face_detailer_denoise)

    return workflow, seed


def build_img2img_workflow(params: Img2ImgParams, comfyui_image_name: str) -> tuple[dict, int]:
    workflow = copy.deepcopy(load_workflow("img2img"))
    seed = params.seed if params.seed is not None else random.randint(0, 2**32 - 1)

    workflow["4"]["inputs"]["ckpt_name"] = params.checkpoint
    workflow["10"]["inputs"]["sampling"] = params.sampling
    workflow["12"]["inputs"]["image"] = comfyui_image_name
    workflow["6"]["inputs"]["text"] = params.prompt
    workflow["7"]["inputs"]["text"] = params.negative_prompt
    workflow["3"]["inputs"]["seed"] = seed
    workflow["3"]["inputs"]["steps"] = params.steps
    workflow["3"]["inputs"]["cfg"] = params.cfg
    workflow["3"]["inputs"]["sampler_name"] = params.sampler
    workflow["3"]["inputs"]["denoise"] = params.denoise

    apply_loras_and_vae(workflow, params.loras, params.vae)
    apply_hires_fix(workflow, params.hires_fix, params.hires_scale, params.hires_denoise)
    apply_face_detailer(workflow, params.face_detailer, params.face_detailer_model, params.face_detailer_denoise)

    return workflow, seed


def build_inpaint_workflow(
    params: InpaintParams, comfyui_image_name: str, comfyui_mask_name: str
) -> tuple[dict, int]:
    workflow = copy.deepcopy(load_workflow("inpaint"))
    seed = params.seed if params.seed is not None else random.randint(0, 2**32 - 1)

    workflow["4"]["inputs"]["ckpt_name"] = params.checkpoint
    workflow["10"]["inputs"]["sampling"] = params.sampling
    workflow["12"]["inputs"]["image"] = comfyui_image_name
    workflow["13"]["inputs"]["image"] = comfyui_mask_name
    workflow["15"]["inputs"]["expand"] = params.grow_mask_by
    if params.inpaint_mode == "fill":
        workflow["14"] = {
            "class_type": "VAEEncodeForInpaint",
            "inputs": {"pixels": ["12", 0], "vae": ["4", 2], "mask": ["20", 0], "grow_mask_by": 0},
        }
        del workflow["11"]
    blur = max(1, min(31, params.mask_blur))
    workflow["19"]["inputs"]["blur_radius"] = blur
    workflow["19"]["inputs"]["sigma"] = max(0.1, min(10.0, blur / 2))
    workflow["6"]["inputs"]["text"] = params.prompt
    workflow["7"]["inputs"]["text"] = params.negative_prompt
    workflow["3"]["inputs"]["seed"] = seed
    workflow["3"]["inputs"]["steps"] = params.steps
    workflow["3"]["inputs"]["cfg"] = params.cfg
    workflow["3"]["inputs"]["sampler_name"] = params.sampler
    # "fill" starts from a blank (grey) latent; anything below 1.0 leaves that grey/green cast behind
    workflow["3"]["inputs"]["denoise"] = 1.0 if params.inpaint_mode == "fill" else params.denoise

    apply_loras_and_vae(workflow, params.loras, params.vae)
    apply_hires_fix(workflow, params.hires_fix, params.hires_scale, params.hires_denoise)
    apply_face_detailer(workflow, params.face_detailer, params.face_detailer_model, params.face_detailer_denoise)

    return workflow, seed


def apply_interpolation(workflow: dict, multiplier: int, model_name: str | None) -> dict:
    """Doubles (or more) the frame rate of a video graph: decoded frames -> RIFE/FILM -> CreateVideo.

    Expects the standard video layout: VAEDecode "8" feeding CreateVideo "11".
    """
    if multiplier <= 1 or not model_name:
        return workflow
    workflow["20"] = {"class_type": "FrameInterpolationModelLoader", "inputs": {"model_name": model_name}}
    workflow["21"] = {
        "class_type": "FrameInterpolate",
        "inputs": {"interp_model": ["20", 0], "images": ["8", 0], "multiplier": multiplier},
    }
    workflow["11"]["inputs"]["images"] = ["21", 0]
    workflow["11"]["inputs"]["fps"] = float(workflow["11"]["inputs"]["fps"]) * multiplier
    return workflow


def build_interpolation_workflow(video_name: str, model_name: str, multiplier: int, fps: float) -> dict:
    """Interpolates an existing video file that sits in ComfyUI's input folder."""
    return {
        "1": {"class_type": "LoadVideo", "inputs": {"file": video_name}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "FrameInterpolationModelLoader", "inputs": {"model_name": model_name}},
        "4": {"class_type": "FrameInterpolate", "inputs": {"interp_model": ["3", 0], "images": ["2", 0], "multiplier": multiplier}},
        "5": {"class_type": "CreateVideo", "inputs": {"images": ["4", 0], "fps": fps * multiplier}},
        "6": {"class_type": "SaveVideo", "inputs": {"video": ["5", 0], "filename_prefix": "video/local_image_ai_smooth", "format": "auto", "codec": "auto"}},
    }


def build_video_workflow(
    *,
    prompt: str,
    negative_prompt: str,
    unet: str,
    text_encoder: str,
    vae: str,
    width: int,
    height: int,
    length: int,
    steps: int,
    cfg: float,
    seed: int | None,
    fps: float,
    image_name: str | None,
) -> tuple[dict, int]:
    """Wan 2.2 TI2V graph: image-to-video when `image_name` is given, text-to-video otherwise."""
    workflow = copy.deepcopy(load_workflow("wan_video"))
    seed = seed if seed is not None else random.randint(0, 2**32 - 1)

    workflow["4"]["inputs"]["unet_name"] = unet
    workflow["5"]["inputs"]["clip_name"] = text_encoder
    workflow["13"]["inputs"]["vae_name"] = vae
    workflow["6"]["inputs"]["text"] = prompt
    workflow["7"]["inputs"]["text"] = negative_prompt
    workflow["3"]["inputs"]["seed"] = seed
    workflow["3"]["inputs"]["steps"] = steps
    workflow["3"]["inputs"]["cfg"] = cfg
    workflow["11"]["inputs"]["fps"] = fps
    workflow["14"]["inputs"].update({"width": width, "height": height, "length": length})
    if image_name:
        workflow["12"]["inputs"]["image"] = image_name
    else:
        del workflow["12"]
        del workflow["14"]["inputs"]["start_image"]
    return workflow, seed


def build_video_hq_workflow(
    *,
    prompt: str,
    negative_prompt: str,
    high_model: str,
    low_model: str,
    high_lora: str,
    low_lora: str,
    text_encoder: str,
    vae: str,
    width: int,
    height: int,
    length: int,
    seed: int | None,
    fps: float,
    image_name: str,
) -> tuple[dict, int]:
    """Wan 2.2 I2V A14B (GGUF) with the 4-step Lightning LoRAs: high-noise expert for the first half of
    the steps, low-noise expert for the rest. Image-to-video only."""
    workflow = copy.deepcopy(load_workflow("wan14b_i2v"))
    seed = seed if seed is not None else random.randint(0, 2**32 - 1)

    workflow["4"]["inputs"]["unet_name"] = high_model
    workflow["16"]["inputs"]["unet_name"] = low_model
    workflow["17"]["inputs"]["lora_name"] = high_lora
    workflow["18"]["inputs"]["lora_name"] = low_lora
    workflow["5"]["inputs"]["clip_name"] = text_encoder
    workflow["13"]["inputs"]["vae_name"] = vae
    workflow["6"]["inputs"]["text"] = prompt
    workflow["7"]["inputs"]["text"] = negative_prompt
    workflow["12"]["inputs"]["image"] = image_name
    workflow["14"]["inputs"].update({"width": width, "height": height, "length": length})
    workflow["3"]["inputs"]["noise_seed"] = seed
    workflow["15"]["inputs"]["noise_seed"] = seed
    workflow["11"]["inputs"]["fps"] = fps
    return workflow, seed


def apply_qwen_loras(workflow: dict, loras: list[LoRAConfig], model_ref: list) -> list:
    """Chains LoraLoaderModelOnly nodes onto a Qwen-Image MODEL ref (these community LoRAs only
    target the diffusion transformer, not the text encoder). Returns the final MODEL ref."""
    next_id = max(int(k) for k in workflow.keys()) + 1
    for lora in loras:
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": model_ref, "lora_name": lora.name, "strength_model": lora.weight},
        }
        model_ref = [node_id, 0]
    return model_ref


def build_qwen_txt2img_workflow(
    params: QwenGenerateParams, unet: str, text_encoder: str, vae: str
) -> tuple[dict, int]:
    workflow = copy.deepcopy(load_workflow("qwen_txt2img"))
    seed = params.seed if params.seed is not None else random.randint(0, 2**32 - 1)

    workflow["4"]["inputs"]["unet_name"] = unet
    workflow["11"]["inputs"]["clip_name"] = text_encoder
    workflow["10"]["inputs"]["vae_name"] = vae
    workflow["5"]["inputs"]["width"] = params.width
    workflow["5"]["inputs"]["height"] = params.height
    workflow["6"]["inputs"]["text"] = params.prompt
    workflow["7"]["inputs"]["text"] = params.negative_prompt
    workflow["3"]["inputs"]["seed"] = seed
    workflow["3"]["inputs"]["steps"] = params.steps
    workflow["3"]["inputs"]["cfg"] = params.cfg
    workflow["3"]["inputs"]["model"] = apply_qwen_loras(workflow, params.loras, ["4", 0])

    return workflow, seed


def build_qwen_edit_workflow(
    params: QwenEditParams, unet: str, text_encoder: str, vae: str, image_name: str, width: int, height: int
) -> tuple[dict, int]:
    """Qwen-Image-Edit: the reference image feeds TextEncodeQwenImageEditPlus (which derives its own
    reference latents), while the KSampler canvas is a plain empty latent at `width`/`height`."""
    workflow = copy.deepcopy(load_workflow("qwen_edit"))
    seed = params.seed if params.seed is not None else random.randint(0, 2**32 - 1)

    workflow["4"]["inputs"]["unet_name"] = unet
    workflow["11"]["inputs"]["clip_name"] = text_encoder
    workflow["10"]["inputs"]["vae_name"] = vae
    workflow["13"]["inputs"]["image"] = image_name
    workflow["5"]["inputs"]["width"] = width
    workflow["5"]["inputs"]["height"] = height
    workflow["6"]["inputs"]["prompt"] = params.prompt
    workflow["3"]["inputs"]["seed"] = seed
    workflow["3"]["inputs"]["steps"] = params.steps
    workflow["3"]["inputs"]["cfg"] = params.cfg
    workflow["12"]["inputs"]["model"] = apply_qwen_loras(workflow, params.loras, ["4", 0])

    return workflow, seed


def build_pose_preprocess_node(image_ref: list) -> dict:
    """DWPose (comfyui_controlnet_aux) node that renders an OpenPose skeleton image."""
    return {
        "class_type": "DWPreprocessor",
        "inputs": {
            "image": image_ref,
            "detect_hand": "enable",
            "detect_body": "enable",
            "detect_face": "enable",
            "resolution": 1024,
            "bbox_detector": "None",
            "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt",
            "scale_stick_for_xinsr_cn": "enable",
        },
    }


def build_controlnet_workflow(
    params: ControlNetGenerateParams, comfyui_control_image_name: str
) -> tuple[dict, int]:
    workflow = copy.deepcopy(load_workflow("controlnet_txt2img"))
    seed = params.seed if params.seed is not None else random.randint(0, 2**32 - 1)

    workflow["4"]["inputs"]["ckpt_name"] = params.checkpoint
    workflow["10"]["inputs"]["sampling"] = params.sampling
    workflow["5"]["inputs"]["width"] = params.width
    workflow["5"]["inputs"]["height"] = params.height
    workflow["6"]["inputs"]["text"] = params.prompt
    workflow["7"]["inputs"]["text"] = params.negative_prompt
    workflow["3"]["inputs"]["seed"] = seed
    workflow["3"]["inputs"]["steps"] = params.steps
    workflow["3"]["inputs"]["cfg"] = params.cfg
    workflow["3"]["inputs"]["sampler_name"] = params.sampler

    workflow["14"]["inputs"]["image"] = comfyui_control_image_name
    workflow["15"]["inputs"]["low_threshold"] = params.canny_low_threshold
    workflow["15"]["inputs"]["high_threshold"] = params.canny_high_threshold
    workflow["16"]["inputs"]["control_net_name"] = params.controlnet_model
    workflow["17"]["inputs"]["strength"] = params.strength
    workflow["17"]["inputs"]["start_percent"] = params.start_percent
    workflow["17"]["inputs"]["end_percent"] = params.end_percent
    if params.preprocessor == "none":
        workflow["17"]["inputs"]["image"] = ["14", 0]
        del workflow["15"]
    elif params.preprocessor == "openpose":
        workflow["15"] = build_pose_preprocess_node(["14", 0])

    apply_loras_and_vae(workflow, params.loras, params.vae)
    apply_hires_fix(workflow, params.hires_fix, params.hires_scale, params.hires_denoise)
    apply_face_detailer(workflow, params.face_detailer, params.face_detailer_model, params.face_detailer_denoise)

    return workflow, seed


def build_ipadapter_workflow(
    params: IPAdapterGenerateParams, comfyui_reference_image_name: str
) -> tuple[dict, int]:
    workflow = copy.deepcopy(load_workflow("txt2img"))
    seed = params.seed if params.seed is not None else random.randint(0, 2**32 - 1)

    workflow["4"]["inputs"]["ckpt_name"] = params.checkpoint
    workflow["10"]["inputs"]["sampling"] = params.sampling
    workflow["5"]["inputs"]["width"] = params.width
    workflow["5"]["inputs"]["height"] = params.height
    workflow["6"]["inputs"]["text"] = params.prompt
    workflow["7"]["inputs"]["text"] = params.negative_prompt
    workflow["3"]["inputs"]["seed"] = seed
    workflow["3"]["inputs"]["steps"] = params.steps
    workflow["3"]["inputs"]["cfg"] = params.cfg
    workflow["3"]["inputs"]["sampler_name"] = params.sampler

    apply_loras_and_vae(workflow, params.loras, params.vae)
    apply_ipadapter(
        workflow,
        True,
        comfyui_reference_image_name,
        params.ipadapter_model,
        params.clip_vision_model,
        params.weight,
        params.weight_type,
        params.start_at,
        params.end_at,
    )
    apply_hires_fix(workflow, params.hires_fix, params.hires_scale, params.hires_denoise)
    apply_face_detailer(workflow, params.face_detailer, params.face_detailer_model, params.face_detailer_denoise)

    return workflow, seed
