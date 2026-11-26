import type { Character,
  ControlNetGenerateParams,
  GenerateParams,
  GenerateResponse,
  GenerationRecord,
  GpuStatus,
  Img2ImgParams,
  InpaintParams,
  HiresFixParams,
  IPAdapterGenerateParams,
  LoRAConfig,
} from '../types'

function setFaceDetailer(form: FormData, p: HiresFixParams) {
  if (p.face_detailer && p.face_detailer_model) {
    form.set('face_detailer', 'true')
    form.set('face_detailer_model', p.face_detailer_model)
    form.set('face_detailer_denoise', String(p.face_detailer_denoise ?? 0.5))
  }
}

function formatErrorDetail(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail
  // FastAPI validation errors (422) send `detail` as a list of {loc, msg, type}
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? `${(d as { loc?: unknown[] }).loc?.slice(-1)[0] ?? ''}: ${(d as { msg: string }).msg}` : JSON.stringify(d)))
      .join('; ')
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail)
  return `요청 실패 (${status})`
}

async function handleJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(formatErrorDetail(body.detail, res.status))
  }
  return res.json() as Promise<T>
}

export async function generateImage(params: GenerateParams): Promise<GenerateResponse> {
  const res = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  return handleJson<GenerateResponse>(res)
}

export async function img2img(
  params: Img2ImgParams,
  source: { file: File } | { sourceGenerationId: number },
): Promise<GenerateResponse> {
  const form = new FormData()
  form.set('prompt', params.prompt)
  form.set('negative_prompt', params.negative_prompt)
  form.set('checkpoint', params.checkpoint)
  if (params.seed !== undefined) form.set('seed', String(params.seed))
  form.set('steps', String(params.steps))
  form.set('cfg', String(params.cfg))
  form.set('sampler', params.sampler)
  form.set('denoise', String(params.denoise))
  form.set('sampling', params.sampling)
  form.set('loras', JSON.stringify(params.loras))
  if (params.vae) form.set('vae', params.vae)
  form.set('hires_fix', String(params.hires_fix))
  form.set('hires_scale', String(params.hires_scale))
  form.set('hires_denoise', String(params.hires_denoise))
  setFaceDetailer(form, params)
  if (params.parent_image_id !== undefined) form.set('parent_image_id', String(params.parent_image_id))

  if ('file' in source) {
    form.set('file', source.file)
  } else {
    form.set('source_generation_id', String(source.sourceGenerationId))
  }

  const res = await fetch('/api/img2img', { method: 'POST', body: form })
  return handleJson<GenerateResponse>(res)
}

export async function inpaint(
  params: InpaintParams,
  maskBlob: Blob,
  source: { file: File } | { sourceGenerationId: number },
): Promise<GenerateResponse> {
  const form = new FormData()
  form.set('prompt', params.prompt)
  form.set('negative_prompt', params.negative_prompt)
  form.set('checkpoint', params.checkpoint)
  if (params.seed !== undefined) form.set('seed', String(params.seed))
  form.set('steps', String(params.steps))
  form.set('cfg', String(params.cfg))
  form.set('sampler', params.sampler)
  form.set('denoise', String(params.denoise))
  form.set('sampling', params.sampling)
  form.set('grow_mask_by', String(params.grow_mask_by))
  form.set('mask_blur', String(params.mask_blur))
  form.set('inpaint_mode', params.inpaint_mode)
  form.set('context_crop', params.context_crop)
  form.set('harmonize', String(params.harmonize))
  form.set('loras', JSON.stringify(params.loras))
  if (params.vae) form.set('vae', params.vae)
  form.set('hires_fix', String(params.hires_fix))
  form.set('hires_scale', String(params.hires_scale))
  form.set('hires_denoise', String(params.hires_denoise))
  setFaceDetailer(form, params)
  if (params.parent_image_id !== undefined) form.set('parent_image_id', String(params.parent_image_id))
  form.set('mask_file', maskBlob, 'mask.png')

  if ('file' in source) {
    form.set('file', source.file)
  } else {
    form.set('source_generation_id', String(source.sourceGenerationId))
  }

  const res = await fetch('/api/inpaint', { method: 'POST', body: form })
  return handleJson<GenerateResponse>(res)
}

export async function getCheckpoints(): Promise<string[]> {
  const res = await fetch('/api/models')
  const data = await handleJson<{ checkpoints: string[] }>(res)
  return data.checkpoints
}

export async function getLoras(): Promise<string[]> {
  const res = await fetch('/api/loras')
  const data = await handleJson<{ loras: string[] }>(res)
  return data.loras
}

export async function getVaes(): Promise<string[]> {
  const res = await fetch('/api/vaes')
  const data = await handleJson<{ vaes: string[] }>(res)
  return data.vaes
}

export async function getControlnets(): Promise<string[]> {
  const res = await fetch('/api/controlnets')
  const data = await handleJson<{ controlnets: string[] }>(res)
  return data.controlnets
}

export async function preprocessCanny(
  file: File,
  lowThreshold: number,
  highThreshold: number,
): Promise<string> {
  const form = new FormData()
  form.set('file', file)
  form.set('low_threshold', String(lowThreshold))
  form.set('high_threshold', String(highThreshold))
  const res = await fetch('/api/preprocess/canny', { method: 'POST', body: form })
  const data = await handleJson<{ image_path: string }>(res)
  return data.image_path
}

export async function preprocessOpenpose(file: File): Promise<string> {
  const form = new FormData()
  form.set('file', file)
  const res = await fetch('/api/preprocess/openpose', { method: 'POST', body: form })
  const data = await handleJson<{ image_path: string }>(res)
  return data.image_path
}

export async function controlnetGenerate(
  params: ControlNetGenerateParams,
  file: File,
): Promise<GenerateResponse> {
  const form = new FormData()
  form.set('file', file)
  form.set('prompt', params.prompt)
  form.set('negative_prompt', params.negative_prompt)
  form.set('checkpoint', params.checkpoint)
  if (params.seed !== undefined) form.set('seed', String(params.seed))
  form.set('steps', String(params.steps))
  form.set('cfg', String(params.cfg))
  form.set('sampler', params.sampler)
  form.set('width', String(params.width))
  form.set('height', String(params.height))
  form.set('sampling', params.sampling)
  form.set('loras', JSON.stringify(params.loras))
  if (params.vae) form.set('vae', params.vae)
  form.set('hires_fix', String(params.hires_fix))
  form.set('hires_scale', String(params.hires_scale))
  form.set('hires_denoise', String(params.hires_denoise))
  setFaceDetailer(form, params)
  form.set('controlnet_model', params.controlnet_model)
  form.set('preprocessor', params.preprocessor)
  form.set('canny_low_threshold', String(params.canny_low_threshold))
  form.set('canny_high_threshold', String(params.canny_high_threshold))
  form.set('strength', String(params.strength))
  form.set('start_percent', String(params.start_percent))
  form.set('end_percent', String(params.end_percent))

  const res = await fetch('/api/controlnet_generate', { method: 'POST', body: form })
  return handleJson<GenerateResponse>(res)
}

export async function getIpadapters(): Promise<string[]> {
  const res = await fetch('/api/ipadapters')
  return (await handleJson<{ ipadapters: string[] }>(res)).ipadapters
}

export async function getClipVisions(): Promise<string[]> {
  const res = await fetch('/api/clip_visions')
  return (await handleJson<{ clip_visions: string[] }>(res)).clip_visions
}

export async function getBboxDetectors(): Promise<string[]> {
  const res = await fetch('/api/bbox_detectors')
  return (await handleJson<{ bbox_detectors: string[] }>(res)).bbox_detectors
}

export async function ipadapterGenerate(
  params: IPAdapterGenerateParams,
  file: File,
): Promise<GenerateResponse> {
  const form = new FormData()
  form.set('file', file)
  form.set('prompt', params.prompt)
  form.set('negative_prompt', params.negative_prompt)
  form.set('checkpoint', params.checkpoint)
  if (params.seed !== undefined) form.set('seed', String(params.seed))
  form.set('steps', String(params.steps))
  form.set('cfg', String(params.cfg))
  form.set('sampler', params.sampler)
  form.set('width', String(params.width))
  form.set('height', String(params.height))
  form.set('sampling', params.sampling)
  form.set('loras', JSON.stringify(params.loras))
  if (params.vae) form.set('vae', params.vae)
  form.set('hires_fix', String(params.hires_fix))
  form.set('hires_scale', String(params.hires_scale))
  form.set('hires_denoise', String(params.hires_denoise))
  setFaceDetailer(form, params)
  form.set('ipadapter_model', params.ipadapter_model)
  form.set('clip_vision_model', params.clip_vision_model)
  form.set('weight', String(params.weight))
  form.set('weight_type', params.weight_type)
  form.set('start_at', String(params.start_at))
  form.set('end_at', String(params.end_at))
  const res = await fetch('/api/ipadapter_generate', { method: 'POST', body: form })
  return handleJson<GenerateResponse>(res)
}

export async function getGeneration(id: number): Promise<GenerationRecord> {
  const res = await fetch(`/api/generation/${id}`)
  return handleJson<GenerationRecord>(res)
}

export async function getHistory(): Promise<GenerationRecord[]> {
  const res = await fetch('/api/history')
  const data = await handleJson<{ generations: GenerationRecord[] }>(res)
  return data.generations
}

export async function getGpuStatus(): Promise<GpuStatus> {
  const res = await fetch('/api/gpu')
  return handleJson<GpuStatus>(res)
}

export async function cancelGeneration(id: number): Promise<void> {
  const res = await fetch(`/api/cancel/${id}`, { method: 'POST' })
  await handleJson(res)
}

export type ProgressMessage =
  | { type: 'status'; status: string }
  | {
      type: 'progress'
      value: number
      max: number
      stage?: string
      done?: number
      total?: number
      sampler?: [number, number] | null
      elapsed?: number
      eta?: number | null
      segment?: [number, number] | null
    }
  | { type: 'stage'; stage: string; elapsed?: number; segment?: [number, number] | null }
  | { type: 'queue'; ahead: number }
  | { type: 'completed'; image_path: string }
  | { type: 'failed'; error: string }
  | { type: 'cancelled' }
  | { type: 'error'; message: string }

export function watchProgress(id: number, onMessage: (msg: ProgressMessage) => void): () => void {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${protocol}://${window.location.host}/api/ws/progress/${id}`)
  ws.onmessage = (event) => {
    try {
      onMessage(JSON.parse(event.data))
    } catch {
      // ignore malformed messages
    }
  }
  return () => ws.close()
}

export interface VideoParams {
  prompt: string
  sourceGenerationId?: number
  file?: File
  length: number
  steps: number
  seed?: number
  quality?: 'auto' | 'fast' | 'high'
  segments?: number
  interpolate?: number
}

export interface VideoModelsInfo {
  available: boolean
  hq_available: boolean
  interpolation_available?: boolean
}

export async function videoInterpolate(generationId: number, multiplier: number): Promise<GenerateResponse> {
  const res = await fetch('/api/video_interpolate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ generation_id: generationId, multiplier }),
  })
  return handleJson<GenerateResponse>(res)
}

export interface TagSuggestion {
  tag: string
  category: string
  count: number
  alias: string | null
}

export async function searchTags(query: string): Promise<TagSuggestion[]> {
  const res = await fetch(`/api/tags?q=${encodeURIComponent(query)}&limit=8`)
  const data = await handleJson<{ tags: TagSuggestion[] }>(res)
  return data.tags
}

export interface LoraInfo {
  name: string
  found: boolean
  readable?: boolean
  title?: string
  base_model?: string
  network_dim?: string
  top_tags?: { tag: string; count: number }[]
  trigger_candidates?: string[]
  has_tag_info?: boolean
}

export async function getLoraInfo(name: string): Promise<LoraInfo> {
  return handleJson<LoraInfo>(await fetch(`/api/lora_info?name=${encodeURIComponent(name)}`))
}

export async function getVideoModels(): Promise<VideoModelsInfo> {
  return handleJson<VideoModelsInfo>(await fetch('/api/video_models'))
}

export async function videoGenerate(params: VideoParams): Promise<GenerateResponse> {
  const form = new FormData()
  form.set('prompt', params.prompt)
  form.set('length', String(params.length))
  form.set('steps', String(params.steps))
  form.set('quality', params.quality ?? 'auto')
  form.set('segments', String(params.segments ?? 1))
  form.set('interpolate', String(params.interpolate ?? 1))
  if (params.seed !== undefined) form.set('seed', String(params.seed))
  if (params.file) form.set('file', params.file)
  else if (params.sourceGenerationId !== undefined) form.set('source_generation_id', String(params.sourceGenerationId))
  const res = await fetch('/api/video_generate', { method: 'POST', body: form })
  return handleJson<GenerateResponse>(res)
}

export interface QwenModelsInfo {
  t2i_available: boolean
  edit_available: boolean
  loras: string[]
}

export interface QwenGenerateParams {
  prompt: string
  negative_prompt: string
  seed?: number
  steps: number
  cfg: number
  width: number
  height: number
  loras: LoRAConfig[]
}

export interface QwenEditParams {
  prompt: string
  seed?: number
  steps: number
  cfg: number
  loras: LoRAConfig[]
}

export async function getQwenModels(): Promise<QwenModelsInfo> {
  return handleJson<QwenModelsInfo>(await fetch('/api/qwen_models'))
}

export async function qwenGenerate(params: QwenGenerateParams): Promise<GenerateResponse> {
  const res = await fetch('/api/qwen_generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  return handleJson<GenerateResponse>(res)
}

export async function qwenEdit(
  params: QwenEditParams,
  source: { file: File } | { sourceGenerationId: number },
): Promise<GenerateResponse> {
  const form = new FormData()
  form.set('prompt', params.prompt)
  if (params.seed !== undefined) form.set('seed', String(params.seed))
  form.set('steps', String(params.steps))
  form.set('cfg', String(params.cfg))
  form.set('loras', JSON.stringify(params.loras))
  if ('file' in source) {
    form.set('file', source.file)
  } else {
    form.set('source_generation_id', String(source.sourceGenerationId))
  }
  const res = await fetch('/api/qwen_edit', { method: 'POST', body: form })
  return handleJson<GenerateResponse>(res)
}

export async function listCharacters(): Promise<Character[]> {
  const data = await handleJson<{ characters: Character[] }>(await fetch('/api/characters'))
  return data.characters
}

export async function createCharacter(form: FormData): Promise<Character> {
  return handleJson<Character>(await fetch('/api/characters', { method: 'POST', body: form }))
}

export async function updateCharacter(id: number, form: FormData): Promise<Character> {
  return handleJson<Character>(await fetch(`/api/characters/${id}`, { method: 'PUT', body: form }))
}

export async function deleteCharacter(id: number): Promise<void> {
  await handleJson<{ deleted: number }>(await fetch(`/api/characters/${id}`, { method: 'DELETE' }))
}

export async function characterFromGeneration(generationId: number, name: string, stripPrefix: string): Promise<Character> {
  const res = await fetch('/api/characters/from_generation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ generation_id: generationId, name, strip_prefix: stripPrefix }),
  })
  return handleJson<Character>(res)
}
