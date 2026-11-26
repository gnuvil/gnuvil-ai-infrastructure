export interface LoRAConfig {
  name: string
  weight: number
}

export interface HiresFixParams {
  hires_fix: boolean
  hires_scale: number
  hires_denoise: number
  face_detailer?: boolean
  face_detailer_model?: string
  face_detailer_denoise?: number
}

export interface IPAdapterGenerateParams extends HiresFixParams {
  prompt: string
  negative_prompt: string
  checkpoint: string
  seed?: number
  steps: number
  cfg: number
  sampler: string
  width: number
  height: number
  sampling: string
  loras: LoRAConfig[]
  vae?: string
  ipadapter_model: string
  clip_vision_model: string
  weight: number
  weight_type: string
  start_at: number
  end_at: number
}

export interface GenerateParams extends HiresFixParams {
  prompt: string
  negative_prompt: string
  checkpoint: string
  seed?: number
  steps: number
  cfg: number
  sampler: string
  width: number
  height: number
  sampling: string
  loras: LoRAConfig[]
  vae?: string
}

export interface ControlNetGenerateParams extends HiresFixParams {
  prompt: string
  negative_prompt: string
  checkpoint: string
  seed?: number
  steps: number
  cfg: number
  sampler: string
  width: number
  height: number
  sampling: string
  loras: LoRAConfig[]
  vae?: string
  controlnet_model: string
  preprocessor: 'none' | 'canny' | 'openpose'
  canny_low_threshold: number
  canny_high_threshold: number
  strength: number
  start_percent: number
  end_percent: number
}

export interface Img2ImgParams extends HiresFixParams {
  prompt: string
  negative_prompt: string
  checkpoint: string
  seed?: number
  steps: number
  cfg: number
  sampler: string
  denoise: number
  sampling: string
  loras: LoRAConfig[]
  vae?: string
  parent_image_id?: number
}

export interface InpaintParams extends HiresFixParams {
  prompt: string
  negative_prompt: string
  checkpoint: string
  seed?: number
  steps: number
  cfg: number
  sampler: string
  denoise: number
  sampling: string
  grow_mask_by: number
  mask_blur: number
  inpaint_mode: 'auto' | 'keep' | 'fill'
  context_crop: 'auto' | 'always' | 'off'
  harmonize: boolean
  loras: LoRAConfig[]
  vae?: string
  parent_image_id?: number
}

export interface GenerateResponse {
  generation_id: number
  prompt_id: string
  status: string
}

export interface GenerationRecord {
  id: number
  prompt: string
  negative_prompt: string
  model: string
  seed: number
  steps: number
  cfg: number
  width: number
  height: number
  sampler: string
  loras?: string
  media_type?: 'image' | 'video'
  frames?: number
  image_path: string | null
  parent_image_id: number | null
  status: string
  created_at: string
}

export interface GpuStatus {
  name: string
  vram_used_mb: number
  vram_total_mb: number
  utilization_percent: number | null
  temperature_c: number | null
}

export interface Character {
  id: number
  name: string
  aliases: string
  tags: string
  loras: LoRAConfig[]
  reference_image: string | null
  use_reference: boolean
  ip_weight: number
  notes: string
}

export interface AppliedCharacter {
  id: number
  name: string
  loras: LoRAConfig[]
  use_reference: boolean
  reference_image: string | null
  ip_weight: number
}
