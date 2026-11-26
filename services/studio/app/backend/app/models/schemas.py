from pydantic import BaseModel, Field
from typing import Optional


class LoRAConfig(BaseModel):
    name: str
    weight: float = Field(default=0.8, ge=-2.0, le=2.0)


class GenerateRequest(BaseModel):
    prompt: str
    prompt_prefix: Optional[str] = None
    negative_prompt: str = ""
    checkpoint: str
    seed: Optional[int] = None
    steps: int = Field(default=28, ge=1, le=150)
    cfg: float = Field(default=7.0, ge=0.0, le=30.0)
    sampler: str = "euler_ancestral"
    width: int = Field(default=1024, ge=64, le=2048)
    height: int = Field(default=1024, ge=64, le=2048)
    sampling: str = Field(default="eps", pattern="^(eps|v_prediction)$")
    rescale_betas_zero_snr: Optional[bool] = None
    loras: list[LoRAConfig] = []
    vae: Optional[str] = None
    hires_fix: bool = False
    hires_scale: float = Field(default=1.5, ge=1.0, le=2.0)
    hires_denoise: float = Field(default=0.5, ge=0.0, le=1.0)
    face_detailer: bool = False
    face_detailer_model: Optional[str] = None
    face_detailer_denoise: float = Field(default=0.5, ge=0.0, le=1.0)
    parent_image_id: Optional[int] = None


class Img2ImgParams(BaseModel):
    prompt: str
    negative_prompt: str = ""
    checkpoint: str
    seed: Optional[int] = None
    steps: int = Field(default=28, ge=1, le=150)
    cfg: float = Field(default=5.0, ge=0.0, le=30.0)
    sampler: str = "euler"
    denoise: float = Field(default=0.6, ge=0.0, le=1.0)
    sampling: str = Field(default="eps", pattern="^(eps|v_prediction)$")
    loras: list[LoRAConfig] = []
    vae: Optional[str] = None
    hires_fix: bool = False
    hires_scale: float = Field(default=1.5, ge=1.0, le=2.0)
    hires_denoise: float = Field(default=0.5, ge=0.0, le=1.0)
    face_detailer: bool = False
    face_detailer_model: Optional[str] = None
    face_detailer_denoise: float = Field(default=0.5, ge=0.0, le=1.0)
    parent_image_id: Optional[int] = None


class InpaintParams(BaseModel):
    prompt: str
    negative_prompt: str = ""
    checkpoint: str
    seed: Optional[int] = None
    steps: int = Field(default=28, ge=1, le=150)
    cfg: float = Field(default=5.0, ge=0.0, le=30.0)
    sampler: str = "euler"
    denoise: float = Field(default=0.85, ge=0.0, le=1.0)
    sampling: str = Field(default="eps", pattern="^(eps|v_prediction)$")
    grow_mask_by: int = Field(default=6, ge=0, le=64)
    mask_blur: int = Field(default=16, ge=0, le=31)
    inpaint_mode: str = Field(default="auto", pattern="^(auto|keep|fill)$")
    loras: list[LoRAConfig] = []
    vae: Optional[str] = None
    hires_fix: bool = False
    hires_scale: float = Field(default=1.5, ge=1.0, le=2.0)
    hires_denoise: float = Field(default=0.5, ge=0.0, le=1.0)
    face_detailer: bool = False
    face_detailer_model: Optional[str] = None
    face_detailer_denoise: float = Field(default=0.5, ge=0.0, le=1.0)
    parent_image_id: Optional[int] = None


class ControlNetGenerateParams(BaseModel):
    prompt: str
    negative_prompt: str = ""
    checkpoint: str
    seed: Optional[int] = None
    steps: int = Field(default=28, ge=1, le=150)
    cfg: float = Field(default=5.0, ge=0.0, le=30.0)
    sampler: str = "euler"
    width: int = Field(default=1024, ge=64, le=2048)
    height: int = Field(default=1024, ge=64, le=2048)
    sampling: str = Field(default="eps", pattern="^(eps|v_prediction)$")
    loras: list[LoRAConfig] = []
    vae: Optional[str] = None
    hires_fix: bool = False
    hires_scale: float = Field(default=1.5, ge=1.0, le=2.0)
    hires_denoise: float = Field(default=0.5, ge=0.0, le=1.0)
    controlnet_model: str
    preprocessor: str = Field(default="canny", pattern="^(none|canny|openpose)$")
    canny_low_threshold: float = Field(default=0.4, ge=0.01, le=0.99)
    canny_high_threshold: float = Field(default=0.8, ge=0.01, le=0.99)
    strength: float = Field(default=0.85, ge=0.0, le=10.0)
    start_percent: float = Field(default=0.0, ge=0.0, le=1.0)
    end_percent: float = Field(default=0.7, ge=0.0, le=1.0)
    face_detailer: bool = False
    face_detailer_model: Optional[str] = None
    face_detailer_denoise: float = Field(default=0.5, ge=0.0, le=1.0)
    parent_image_id: Optional[int] = None


class IPAdapterGenerateParams(BaseModel):
    prompt: str
    negative_prompt: str = ""
    checkpoint: str
    seed: Optional[int] = None
    steps: int = Field(default=28, ge=1, le=150)
    cfg: float = Field(default=5.0, ge=0.0, le=30.0)
    sampler: str = "euler"
    width: int = Field(default=1024, ge=64, le=2048)
    height: int = Field(default=1024, ge=64, le=2048)
    sampling: str = Field(default="eps", pattern="^(eps|v_prediction)$")
    loras: list[LoRAConfig] = []
    vae: Optional[str] = None
    hires_fix: bool = False
    hires_scale: float = Field(default=1.5, ge=1.0, le=2.0)
    hires_denoise: float = Field(default=0.5, ge=0.0, le=1.0)
    face_detailer: bool = False
    face_detailer_model: Optional[str] = None
    face_detailer_denoise: float = Field(default=0.5, ge=0.0, le=1.0)
    ipadapter_model: str
    clip_vision_model: str
    weight: float = Field(default=0.2, ge=-1.0, le=5.0)
    weight_type: str = "linear"
    start_at: float = Field(default=0.0, ge=0.0, le=1.0)
    end_at: float = Field(default=1.0, ge=0.0, le=1.0)
    parent_image_id: Optional[int] = None


class QwenGenerateParams(BaseModel):
    # steps/cfg/width/height share the same bounds as GenerateRequest: the frontend reuses that
    # one sidebar state for every mode, so a narrower range here would reject values the UI itself allows.
    prompt: str
    negative_prompt: str = ""
    seed: Optional[int] = None
    steps: int = Field(default=20, ge=1, le=150)
    cfg: float = Field(default=4.5, ge=0.0, le=30.0)
    width: int = Field(default=1024, ge=64, le=2048)
    height: int = Field(default=1024, ge=64, le=2048)
    loras: list[LoRAConfig] = []
    parent_image_id: Optional[int] = None


class QwenEditParams(BaseModel):
    prompt: str
    seed: Optional[int] = None
    steps: int = Field(default=20, ge=1, le=150)
    cfg: float = Field(default=4.0, ge=0.0, le=30.0)
    loras: list[LoRAConfig] = []
    parent_image_id: Optional[int] = None


class GenerateResponse(BaseModel):
    generation_id: int
    prompt_id: str
    status: str


class GenerationRecord(BaseModel):
    id: int
    prompt: str
    negative_prompt: str
    model: str
    loras: list[str] = []
    seed: int
    steps: int
    cfg: float
    width: int
    height: int
    sampler: str
    parent_image_id: Optional[int] = None
    image_path: Optional[str] = None
    status: str
    created_at: str
