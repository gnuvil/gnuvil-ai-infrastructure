import { useEffect, useRef, useState, type CSSProperties } from 'react'
import {
  videoGenerate,
  getVideoModels,
  videoInterpolate,
  getLoraInfo,
  listCharacters,
  characterFromGeneration,
  cancelGeneration,
  controlnetGenerate,
  generateImage,
  getCheckpoints,
  getBboxDetectors,
  getClipVisions,
  getControlnets,
  getIpadapters,
  ipadapterGenerate,
  getGpuStatus,
  getHistory,
  getLoras,
  getQwenModels,
  getVaes,
  img2img,
  inpaint,
  preprocessCanny,
  preprocessOpenpose,
  qwenEdit,
  qwenGenerate,
  watchProgress,
} from './services/api'
import type { AppliedCharacter, Character, GenerationRecord, GpuStatus, LoRAConfig } from './types'
import type { LoraInfo } from './services/api'
import MaskEditor from './components/MaskEditor'
import TagSuggestField from './components/TagSuggestField'
import CharacterPanel from './components/CharacterPanel'
import ApiDashboard from './components/ApiDashboard'
import Gallery from './components/Gallery'

const SAMPLERS = ['euler', 'euler_ancestral', 'dpmpp_2m', 'dpmpp_sde', 'ddim', 'lcm']

const DEFAULT_PROMPT_PREFIX = 'masterpiece, best quality, newest, absurdres, highres,'
const DEFAULT_NEGATIVE_PROMPT =
  'worst quality, old, early, low quality, lowres, signature, username, logo, bad hands, mutated hands, mammal, anthro, furry, ambiguous form, feral, semi-anthro'

interface ChatMessage {
  id: string
  role: 'user' | 'ai'
  text?: string
  imageUrl?: string
  sourceImageUrl?: string
  generationId?: number
  aspect?: number
  status?: 'queued' | 'running' | 'done' | 'error' | 'cancelled' | 'note'
  note?: string
  progress?: {
    value: number
    max: number
    done?: number
    total?: number
    sampler?: [number, number] | null
    eta?: number | null
  }
  stage?: string
  elapsed?: number
  startedAt?: number
  segment?: [number, number] | null
  ahead?: number
}

type SourceImage =
  | { kind: 'upload'; file: File; previewUrl: string }
  | { kind: 'generation'; generationId: number; imageUrl: string }

function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function isVideoUrl(url?: string | null): boolean {
  return !!url && /\.(mp4|webm|mkv)$/i.test(url)
}

/** Overall sampling progress of a running message, 0-100. */
function genPercent(m: { progress?: { value: number; max: number; done?: number; total?: number } }): number {
  const p = m.progress
  if (!p) return 0
  if (p.total) return Math.min(100, Math.round(((p.done ?? 0) / p.total) * 100))
  return Math.min(100, Math.round((p.value / Math.max(1, p.max)) * 100))
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 20 20" className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M5 8l5 5 5-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

const EXAMPLE_PROMPTS = [
  '1girl, silver hair, school uniform, cherry blossoms, wind, smile',
  'scenery, night city, neon lights, rain, reflections, cinematic',
  '1boy, knight, armor, cape, castle, sunset, dramatic lighting',
  'chibi, cat ears, hoodie, cozy room, warm light',
]

/** Same rule as the old server-side merge: character tags first, then the request's tags without duplicates. */
function mergeCharacterTags(chars: Character[], prompt: string): string {
  const split = (t: string) => t.split(',').map((x) => x.trim()).filter(Boolean)
  const out: string[] = []
  const seen = new Set<string>()
  for (const c of chars)
    for (const t of split(c.tags))
      if (!seen.has(t.toLowerCase())) {
        out.push(t)
        seen.add(t.toLowerCase())
      }
  for (const t of split(prompt))
    if (!seen.has(t.toLowerCase())) {
      out.push(t)
      seen.add(t.toLowerCase())
    }
  return out.join(', ')
}

function App() {
  const [dashboardMode, setDashboardMode] = useState(false)
  const [galleryMode, setGalleryMode] = useState(false)
  const [checkpoints, setCheckpoints] = useState<string[]>([])
  const [checkpoint, setCheckpoint] = useState('')
  const [checkpointError, setCheckpointError] = useState<string | null>(null)

  const [prompt, setPrompt] = useState('')
  const [promptPrefix, setPromptPrefix] = useState(DEFAULT_PROMPT_PREFIX)
  const [negativePrompt, setNegativePrompt] = useState(DEFAULT_NEGATIVE_PROMPT)
  const [seed, setSeed] = useState('')
  const [steps, setSteps] = useState(28)
  const [cfg, setCfg] = useState(5.0)
  const [sampler, setSampler] = useState('euler')
  const [width, setWidth] = useState(1024)
  const [height, setHeight] = useState(1024)
  const [sampling, setSampling] = useState('v_prediction')
  const [denoise, setDenoise] = useState(0.6)
  const [maskBlur, setMaskBlur] = useState(16)
  const [inpaintMode, setInpaintMode] = useState<'auto' | 'keep' | 'fill'>('auto')

  const [availableLoras, setAvailableLoras] = useState<string[]>([])
  const [availableVaes, setAvailableVaes] = useState<string[]>([])
  const [selectedLoras, setSelectedLoras] = useState<LoRAConfig[]>([])
  const [vae, setVae] = useState('')

  const [hiresFix, setHiresFix] = useState(false)
  const [hiresScale, setHiresScale] = useState(1.5)
  const [hiresDenoise, setHiresDenoise] = useState(0.5)

  const [faceDetailer, setFaceDetailer] = useState(false)
  const [availableBbox, setAvailableBbox] = useState<string[]>([])
  const [faceModels, setFaceModels] = useState<string[]>([])
  const [faceDenoise, setFaceDenoise] = useState(0.4)

  const [ipOpen, setIpOpen] = useState(false)
  const [ipEnabled, setIpEnabled] = useState(false)
  const [availableIpadapters, setAvailableIpadapters] = useState<string[]>([])
  const [availableClipVisions, setAvailableClipVisions] = useState<string[]>([])
  const [ipModel, setIpModel] = useState('')
  const [clipVisionModel, setClipVisionModel] = useState('')
  const [ipWeight, setIpWeight] = useState(0.2)
  const [ipStart, setIpStart] = useState(0.0)
  const [ipEnd, setIpEnd] = useState(1.0)
  const [ipImage, setIpImage] = useState<{ file: File; previewUrl: string } | null>(null)
  const ipFileInputRef = useRef<HTMLInputElement>(null)

  const [sourceImage, setSourceImage] = useState<SourceImage | null>(null)
  const [maskBlob, setMaskBlob] = useState<Blob | null>(null)
  const [maskEditorOpen, setMaskEditorOpen] = useState(false)

  const [qwenOpen, setQwenOpen] = useState(false)
  const [qwenEnabled, setQwenEnabled] = useState(false)
  const [qwenAvailable, setQwenAvailable] = useState({ t2i: false, edit: false })
  const [availableQwenLoras, setAvailableQwenLoras] = useState<string[]>([])
  const [qwenSelectedLoras, setQwenSelectedLoras] = useState<LoRAConfig[]>([])

  const [controlnetOpen, setControlnetOpen] = useState(false)
  const [controlnetEnabled, setControlnetEnabled] = useState(false)
  const [availableControlnets, setAvailableControlnets] = useState<string[]>([])
  const [controlnetModel, setControlnetModel] = useState('')
  const [controlnetPreprocessor, setControlnetPreprocessor] = useState<'none' | 'canny' | 'openpose'>('canny')
  const [controlnetImage, setControlnetImage] = useState<{ file: File; previewUrl: string } | null>(null)
  const [cannyLow, setCannyLow] = useState(0.4)
  const [cannyHigh, setCannyHigh] = useState(0.8)
  const [cnStrength, setCnStrength] = useState(0.85)
  const [cnStart, setCnStart] = useState(0.0)
  const [cnEnd, setCnEnd] = useState(0.7)
  const [cannyPreviewUrl, setCannyPreviewUrl] = useState<string | null>(null)
  const [cannyPreviewLoading, setCannyPreviewLoading] = useState(false)
  const controlnetFileInputRef = useRef<HTMLInputElement>(null)

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [characters, setCharacters] = useState<Character[]>([])
  const [activeCharacterIds, setActiveCharacterIds] = useState<number[]>([])
  const [characterOpen, setCharacterOpen] = useState(false)
  const [loraInfoOpen, setLoraInfoOpen] = useState<string | null>(null)
  const [loraInfos, setLoraInfos] = useState<Record<string, LoraInfo | 'loading' | 'error'>>({})
  const [clock, setClock] = useState(() => Date.now())
  const [videoSource, setVideoSource] = useState<{ generationId: number; imageUrl: string } | null>(null)
  const [videoLength, setVideoLength] = useState(49)
  const [videoQuality, setVideoQuality] = useState<'auto' | 'fast' | 'high'>('auto')
  const [hqAvailable, setHqAvailable] = useState(false)
  const [videoSegments, setVideoSegments] = useState(1)
  const [videoInterp, setVideoInterp] = useState(1)
  const [interpAvailable, setInterpAvailable] = useState(false)
  const [videoSteps, setVideoSteps] = useState(20)
  const [contextCrop, setContextCrop] = useState<'auto' | 'always' | 'off'>('auto')
  const [harmonize, setHarmonize] = useState(false)
  const [gpu, setGpu] = useState<GpuStatus | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<GenerationRecord[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)

  // ComfyUI is a switchable service on the server: load the model lists once on open and again each
  // time the GPU poll below sees ComfyUI come up. (No blind retry loop: it flooded the backend while off.)
  const comfyOnline = gpu !== null
  useEffect(() => {
    if (!comfyOnline && checkpoints.length > 0) return

    function loadAll() {
      getCheckpoints()
        .then((list) => {
          setCheckpoints(list)
          setCheckpointError(null)
          if (list.length > 0) setCheckpoint((prev) => prev || list[0])
        })
        .catch((e) => setCheckpointError(e.message))
      getLoras().then(setAvailableLoras).catch(() => setAvailableLoras([]))
      getVaes().then(setAvailableVaes).catch(() => setAvailableVaes([]))
      getControlnets()
        .then((list) => {
          setAvailableControlnets(list)
          if (list.length > 0) setControlnetModel((prev) => prev || list[0])
        })
        .catch(() => setAvailableControlnets([]))
      getBboxDetectors()
        .then((list) => {
          setAvailableBbox(list)
          const face = list.find((n) => n.toLowerCase().includes('face'))
          const first = face ?? list[0]
          setFaceModels((prev) => (prev.length > 0 ? prev : first ? [first] : []))
        })
        .catch(() => setAvailableBbox([]))
      getIpadapters()
        .then((list) => {
          setAvailableIpadapters(list)
          if (list.length > 0) setIpModel((prev) => prev || list[0])
        })
        .catch(() => setAvailableIpadapters([]))
      getClipVisions()
        .then((list) => {
          setAvailableClipVisions(list)
          if (list.length > 0) setClipVisionModel((prev) => prev || list[0])
        })
        .catch(() => setAvailableClipVisions([]))
      getQwenModels()
        .then((m) => {
          setQwenAvailable({ t2i: m.t2i_available, edit: m.edit_available })
          setAvailableQwenLoras(m.loras)
        })
        .catch(() => {
          setQwenAvailable({ t2i: false, edit: false })
          setAvailableQwenLoras([])
        })
    }

    loadAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload only when ComfyUI goes on/off
  }, [comfyOnline])

  useEffect(() => {
    const poll = () => getGpuStatus().then(setGpu).catch(() => setGpu(null))
    poll()
    const interval = setInterval(poll, 5000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  function pickFile() {
    fileInputRef.current?.click()
  }

  function onFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setSourceImage({ kind: 'upload', file, previewUrl: URL.createObjectURL(file) })
    setMaskBlob(null)
    e.target.value = ''
  }

  const anyRunning = messages.some((m) => m.status === 'running')

  useEffect(() => {
    if (!anyRunning) return
    const t = setInterval(() => setClock(Date.now()), 1000)
    return () => clearInterval(t)
  }, [anyRunning])

  const videoHq = videoQuality === 'high' || (videoQuality === 'auto' && hqAvailable)

  useEffect(() => {
    if (!videoSource) return
    getVideoModels()
      .then((m) => {
        setHqAvailable(m.hq_available)
        setInterpAvailable(!!m.interpolation_available)
      })
      .catch(() => setHqAvailable(false))
  }, [videoSource])

  useEffect(() => {
    setVideoLength(videoHq ? 81 : 49)
    if (!videoHq) setVideoSegments(1)
  }, [videoHq])

  async function smoothVideo(generationId: number) {
    const aiMsgId = crypto.randomUUID()
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: 'user', text: '🎞 이 영상을 부드럽게 (프레임 2배)' },
      { id: aiMsgId, role: 'ai', status: 'queued', note: '🎞 프레임 보간(RIFE) 2배: 원본 영상은 그대로 두고 새 영상을 만듭니다.' },
    ])
    try {
      const res = await videoInterpolate(generationId, 2)
      setMessages((prev) => prev.map((m) => (m.id === aiMsgId ? { ...m, generationId: res.generation_id } : m)))
      const stop = watchProgress(res.generation_id, (msg) => {
        const patch = (fn: (m: ChatMessage) => ChatMessage) => setMessages((prev) => prev.map((m) => (m.id === aiMsgId ? fn(m) : m)))
        if (msg.type === 'status') patch((m) => ({ ...m, status: 'running' }))
        else if (msg.type === 'queue') patch((m) => ({ ...m, ahead: msg.ahead }))
        else if (msg.type === 'stage') patch((m) => ({ ...m, status: 'running', stage: msg.stage, startedAt: msg.elapsed !== undefined ? Date.now() - msg.elapsed * 1000 : m.startedAt }))
        else if (msg.type === 'progress')
          patch((m) => ({
            ...m,
            status: 'running',
            progress: { value: msg.value, max: msg.max },
            stage: msg.stage ?? m.stage,
            startedAt: msg.elapsed !== undefined ? Date.now() - msg.elapsed * 1000 : m.startedAt,
          }))
        else if (msg.type === 'completed') {
          patch((m) => ({ ...m, status: 'done', imageUrl: msg.image_path }))
          stop()
        } else if (msg.type === 'failed' || msg.type === 'error') {
          patch((m) => ({ ...m, status: 'error', text: msg.type === 'failed' ? msg.error : msg.message }))
          stop()
        } else if (msg.type === 'cancelled') {
          patch((m) => ({ ...m, status: 'cancelled' }))
          stop()
        }
      })
    } catch (e) {
      setMessages((prev) => prev.map((m) => (m.id === aiMsgId ? { ...m, status: 'error', text: (e as Error).message } : m)))
    }
  }

  function startVideo(generationId: number, imageUrl: string) {
    clearSource()
    setVideoSource({ generationId, imageUrl })
  }

  function continueEditing(generationId: number, imageUrl: string) {
    setSourceImage({ kind: 'generation', generationId, imageUrl })
    setMaskBlob(null)
  }

  function clearSource() {
    setSourceImage(null)
    setMaskBlob(null)
  }

  function sourceImageUrl(): string | null {
    if (!sourceImage) return null
    return sourceImage.kind === 'upload' ? sourceImage.previewUrl : sourceImage.imageUrl
  }

  async function loadHistory() {
    setHistoryLoading(true)
    try {
      setHistory(await getHistory())
    } catch {
      setHistory([])
    } finally {
      setHistoryLoading(false)
    }
  }

  function toggleHistory() {
    const next = !historyOpen
    setHistoryOpen(next)
    if (next) loadHistory()
  }

  function addLora(name: string) {
    if (!name || selectedLoras.some((l) => l.name === name)) return
    setSelectedLoras((prev) => [...prev, { name, weight: 0.8 }])
  }

  function refreshCharacters() {
    listCharacters()
      .then((list) => {
        setCharacters(list)
        setActiveCharacterIds((prev) => prev.filter((id) => list.some((c) => c.id === id)))
      })
      .catch(() => setCharacters([]))
  }

  useEffect(() => {
    refreshCharacters()
  }, [])

  useEffect(() => {
    // the smooth button on existing videos needs to know about the model before a video source is chosen
    getVideoModels()
      .then((m) => setInterpAvailable(!!m.interpolation_available))
      .catch(() => setInterpAvailable(false))
  }, [])

  async function saveAsCharacter(generationId: number) {
    const name = window.prompt('이 이미지의 캐릭터 이름을 입력하세요 (이름을 쓰면 이후 요청에 태그와 LoRA가 자동 적용됩니다)')
    if (!name || !name.trim()) return
    try {
      await characterFromGeneration(generationId, name.trim(), promptPrefix.trim())
      refreshCharacters()
      setCharacterOpen(true)
    } catch (e) {
      window.alert((e as Error).message)
    }
  }

  function toggleLoraInfo(name: string) {
    if (loraInfoOpen === name) {
      setLoraInfoOpen(null)
      return
    }
    setLoraInfoOpen(name)
    if (!loraInfos[name]) {
      setLoraInfos((prev) => ({ ...prev, [name]: 'loading' }))
      getLoraInfo(name)
        .then((info) => setLoraInfos((prev) => ({ ...prev, [name]: info })))
        .catch(() => setLoraInfos((prev) => ({ ...prev, [name]: 'error' })))
    }
  }

  function addToPrompt(tag: string) {
    setPrompt((p) => (p.trim() ? `${p.trim().replace(/,$/, '')}, ${tag}` : tag))
  }

  function updateLoraWeight(name: string, weight: number) {
    setSelectedLoras((prev) => prev.map((l) => (l.name === name ? { ...l, weight } : l)))
  }

  function removeLora(name: string) {
    setSelectedLoras((prev) => prev.filter((l) => l.name !== name))
  }

  function addQwenLora(name: string) {
    if (!name || qwenSelectedLoras.some((l) => l.name === name)) return
    setQwenSelectedLoras((prev) => [...prev, { name, weight: 1.0 }])
  }

  function updateQwenLoraWeight(name: string, weight: number) {
    setQwenSelectedLoras((prev) => prev.map((l) => (l.name === name ? { ...l, weight } : l)))
  }

  function removeQwenLora(name: string) {
    setQwenSelectedLoras((prev) => prev.filter((l) => l.name !== name))
  }

  function onIpFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setIpImage({ file, previewUrl: URL.createObjectURL(file) })
    e.target.value = ''
  }

  function pickControlnetFile() {
    controlnetFileInputRef.current?.click()
  }

  function onControlnetFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setControlnetImage({ file, previewUrl: URL.createObjectURL(file) })
    setCannyPreviewUrl(null)
    e.target.value = ''
  }

  async function handleCannyPreview() {
    if (!controlnetImage) return
    setCannyPreviewLoading(true)
    try {
      setCannyPreviewUrl(
        controlnetPreprocessor === 'openpose'
          ? await preprocessOpenpose(controlnetImage.file)
          : await preprocessCanny(controlnetImage.file, cannyLow, cannyHigh),
      )
    } catch {
      setCannyPreviewUrl(null)
    } finally {
      setCannyPreviewLoading(false)
    }
  }

  async function handleGenerate() {
    if (!prompt.trim() || (!checkpoint && !qwenEnabled)) return

    let activeSource: SourceImage | null = sourceImage
    let promptText = prompt
    let effDenoise = denoise
    let effNegative = negativePrompt
    let agentNote = ''
    const attachment = sourceImage?.kind === 'upload' ? sourceImage : null
    const manualMode =
      qwenEnabled || sourceImage?.kind === 'generation' || !!maskBlob || (controlnetEnabled && !!controlnetImage) || (ipEnabled && !!ipImage)
    let autoCnFile: File | null = null
    let autoCnModel: string | null = null
    let appliedChars: AppliedCharacter[] = []
    let charLoraList: LoRAConfig[] = []
    let autoCnPre: 'canny' | 'openpose' | null = null
    let autoCnStrength: number | null = null
    let autoIpWeight: number | null = null
    let autoIpFile: File | null = null
    const autoFaceModels: string[] = []
    let autoHires = false
    const rawPrompt = prompt
    let videoPlan: { prompt: string; file?: File; sourceGenId?: number } | null = null

    // Character profiles (formerly applied by the LLM agent): tags first, then LoRAs, and the
    // reference image as IP-Adapter for plain text-to-image requests.
    const chars = characters.filter((c) => activeCharacterIds.includes(c.id))
    if (chars.length > 0 && !qwenEnabled && !videoSource) {
      appliedChars = chars.map((c) => ({
        id: c.id,
        name: c.name,
        loras: c.loras,
        use_reference: c.use_reference,
        reference_image: c.reference_image,
        ip_weight: c.ip_weight,
      }))
      promptText = mergeCharacterTags(chars, promptText)
      charLoraList = appliedChars.flatMap((c) => c.loras)
      agentNote = `👤 캐릭터 적용: ${appliedChars.map((c) => c.name).join(', ')}`
      const refChar = appliedChars.find((c) => c.use_reference && c.reference_image)
      if (refChar && !manualMode && !activeSource && availableIpadapters.length > 0 && ipModel && clipVisionModel) {
        try {
          const refRes = await fetch(refChar.reference_image!)
          const blob = await refRes.blob()
          if (!refRes.ok || !blob.type.startsWith('image/')) throw new Error('reference is not an image')
          autoIpFile = new File([blob], 'character_ref.png', { type: blob.type || 'image/png' })
          autoIpWeight = refChar.ip_weight
          agentNote += `\n✨ 캐릭터 참조 이미지(IP-Adapter, ${refChar.name})`
        } catch {
          agentNote += '\n⚠ 캐릭터 참조 이미지를 불러오지 못해 태그와 LoRA만 적용합니다.'
        }
      }
    }

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      text: rawPrompt,
      sourceImageUrl: activeSource
        ? activeSource.kind === 'upload'
          ? activeSource.previewUrl
          : activeSource.imageUrl
        : attachment
          ? attachment.previewUrl
          : videoSource
            ? videoSource.imageUrl
            : undefined,
    }
    if (!videoPlan && videoSource) videoPlan = { prompt: rawPrompt, sourceGenId: videoSource.generationId }
    const effLoras = [...selectedLoras, ...charLoraList.filter((l) => !selectedLoras.some((s) => s.name === l.name))]
    const activeMask = maskBlob
    const aiMsgId = crypto.randomUUID()
    setMessages((prev) => [...prev, userMsg, { id: aiMsgId, role: 'ai', status: 'queued', note: agentNote || undefined, aspect: width / Math.max(1, height) }])
    setPrompt('')
    clearSource()
    setVideoSource(null)

    const fullPrompt = promptPrefix.trim() ? `${promptPrefix.trim()} ${promptText}` : promptText

    const cnFile = autoCnFile ?? (controlnetEnabled && controlnetImage ? controlnetImage.file : null)
    const activeControlnetImage = cnFile ? { file: cnFile } : null
    const ipFile = autoIpFile ?? (ipEnabled && ipImage ? ipImage.file : null)
    const activeIpImage = ipFile && !activeControlnetImage ? { file: ipFile } : null
    const hiresOn = hiresFix || autoHires
    const faceModelList = Array.from(new Set([...(faceDetailer ? faceModels : []), ...autoFaceModels]))
    const face = {
      face_detailer: faceModelList.length > 0,
      face_detailer_model: faceModelList.length > 0 ? faceModelList.join(',') : undefined,
      face_detailer_denoise: faceDenoise,
    }

    try {
      let generationId: number

      if (videoPlan) {
        const res = await videoGenerate({
          prompt: videoPlan.prompt,
          file: videoPlan.file,
          sourceGenerationId: videoPlan.sourceGenId,
          length: videoLength,
          steps: videoSteps,
          quality: videoQuality,
          segments: videoSegments,
          interpolate: videoInterp,
          seed: seed ? Number(seed) : undefined,
        })
        generationId = res.generation_id
      } else if (qwenEnabled && (attachment || activeSource?.kind === 'generation')) {
        const genSource = activeSource?.kind === 'generation' ? activeSource : null
        const res = await qwenEdit(
          { prompt: fullPrompt, seed: seed ? Number(seed) : undefined, steps, cfg, loras: qwenSelectedLoras },
          attachment ? { file: attachment.file } : { sourceGenerationId: genSource!.generationId },
        )
        generationId = res.generation_id
      } else if (qwenEnabled) {
        const res = await qwenGenerate({
          prompt: fullPrompt,
          negative_prompt: effNegative,
          seed: seed ? Number(seed) : undefined,
          steps,
          cfg,
          width,
          height,
          loras: qwenSelectedLoras,
        })
        generationId = res.generation_id
      } else if (activeIpImage) {
        const res = await ipadapterGenerate(
          {
            prompt: fullPrompt,
            negative_prompt: effNegative,
            checkpoint,
            seed: seed ? Number(seed) : undefined,
            steps,
            cfg,
            sampler,
            width,
            height,
            sampling,
            loras: effLoras,
            vae: vae || undefined,
            hires_fix: hiresOn,
            hires_scale: hiresScale,
            hires_denoise: hiresDenoise,
            ...face,
            ipadapter_model: ipModel,
            clip_vision_model: clipVisionModel,
            weight: autoIpWeight ?? ipWeight,
            weight_type: 'linear',
            start_at: ipStart,
            end_at: ipEnd,
          },
          activeIpImage.file,
        )
        generationId = res.generation_id
      } else if (activeControlnetImage) {
        const res = await controlnetGenerate(
          {
            prompt: fullPrompt,
            negative_prompt: effNegative,
            checkpoint,
            seed: seed ? Number(seed) : undefined,
            steps,
            cfg,
            sampler,
            width,
            height,
            sampling,
            loras: effLoras,
            vae: vae || undefined,
            hires_fix: hiresOn,
            hires_scale: hiresScale,
            hires_denoise: hiresDenoise,
            ...face,
            controlnet_model: autoCnModel ?? controlnetModel,
            preprocessor: autoCnPre ?? controlnetPreprocessor,
            canny_low_threshold: cannyLow,
            canny_high_threshold: cannyHigh,
            strength: autoCnStrength ?? cnStrength,
            start_percent: cnStart,
            end_percent: cnEnd,
          },
          activeControlnetImage.file,
        )
        generationId = res.generation_id
      } else if (activeSource && activeMask) {
        const res = await inpaint(
          {
            prompt: fullPrompt,
            negative_prompt: effNegative,
            checkpoint,
            seed: seed ? Number(seed) : undefined,
            steps,
            cfg,
            sampler,
            denoise: effDenoise,
            sampling,
            grow_mask_by: 6,
            mask_blur: maskBlur,
            inpaint_mode: inpaintMode,
            context_crop: contextCrop,
            harmonize,
            loras: effLoras,
            vae: vae || undefined,
            hires_fix: hiresOn,
            hires_scale: hiresScale,
            hires_denoise: hiresDenoise,
            ...face,
            parent_image_id: activeSource.kind === 'generation' ? activeSource.generationId : undefined,
          },
          activeMask,
          activeSource.kind === 'upload'
            ? { file: activeSource.file }
            : { sourceGenerationId: activeSource.generationId },
        )
        generationId = res.generation_id
      } else if (activeSource) {
        const res = await img2img(
          {
            prompt: fullPrompt,
            negative_prompt: effNegative,
            checkpoint,
            seed: seed ? Number(seed) : undefined,
            steps,
            cfg,
            sampler,
            denoise: effDenoise,
            sampling,
            loras: effLoras,
            vae: vae || undefined,
            hires_fix: hiresOn,
            hires_scale: hiresScale,
            hires_denoise: hiresDenoise,
            ...face,
            parent_image_id: activeSource.kind === 'generation' ? activeSource.generationId : undefined,
          },
          activeSource.kind === 'upload'
            ? { file: activeSource.file }
            : { sourceGenerationId: activeSource.generationId },
        )
        generationId = res.generation_id
      } else {
        const res = await generateImage({
          prompt: fullPrompt,
          prompt_prefix: promptPrefix.trim(),
          negative_prompt: effNegative,
          checkpoint,
          seed: seed ? Number(seed) : undefined,
          steps,
          cfg,
          sampler,
          width,
          height,
          sampling,
          loras: effLoras,
          vae: vae || undefined,
          hires_fix: hiresOn,
          hires_scale: hiresScale,
          hires_denoise: hiresDenoise,
            ...face,
        })
        generationId = res.generation_id
      }

      setMessages((prev) => prev.map((m) => (m.id === aiMsgId ? { ...m, generationId } : m)))

      await new Promise<void>((resolve) => {
        const stop = watchProgress(generationId, (msg) => {
          if (msg.type === 'status') {
            setMessages((prev) => prev.map((m) => (m.id === aiMsgId ? { ...m, status: 'running' } : m)))
          } else if (msg.type === 'stage') {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === aiMsgId
                  ? { ...m, status: 'running', stage: msg.stage, elapsed: msg.elapsed ?? m.elapsed, startedAt: msg.elapsed !== undefined ? Date.now() - msg.elapsed * 1000 : m.startedAt, segment: msg.segment ?? m.segment }
                  : m,
              ),
            )
          } else if (msg.type === 'queue') {
            setMessages((prev) => prev.map((m) => (m.id === aiMsgId ? { ...m, ahead: msg.ahead } : m)))
          } else if (msg.type === 'progress') {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === aiMsgId
                  ? {
                      ...m,
                      status: 'running',
                      progress: { value: msg.value, max: msg.max, done: msg.done, total: msg.total, sampler: msg.sampler, eta: msg.eta },
                      stage: msg.stage ?? m.stage,
                      elapsed: msg.elapsed ?? m.elapsed,
                      startedAt: msg.elapsed !== undefined ? Date.now() - msg.elapsed * 1000 : m.startedAt,
                      segment: msg.segment ?? m.segment,
                    }
                  : m,
              ),
            )
          } else if (msg.type === 'completed') {
            setMessages((prev) =>
              prev.map((m) => (m.id === aiMsgId ? { ...m, status: 'done', imageUrl: msg.image_path } : m)),
            )
            stop()
            resolve()
          } else if (msg.type === 'failed' || msg.type === 'error') {
            const errText = msg.type === 'failed' ? msg.error : msg.message
            setMessages((prev) =>
              prev.map((m) => (m.id === aiMsgId ? { ...m, status: 'error', text: errText } : m)),
            )
            stop()
            resolve()
          } else if (msg.type === 'cancelled') {
            setMessages((prev) => prev.map((m) => (m.id === aiMsgId ? { ...m, status: 'cancelled' } : m)))
            stop()
            resolve()
          }
        })
      })
    } catch (e) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === aiMsgId ? { ...m, status: 'error', text: (e as Error).message } : m,
        ),
      )
    }
  }

  async function handleCancel(generationId?: number) {
    if (!generationId) return
    try {
      await cancelGeneration(generationId)
    } catch {
      // best-effort; the ws stream will still reflect the final state
    }
  }

  return (
    dashboardMode ? <ApiDashboard onBack={() => setDashboardMode(false)} /> :
    galleryMode ? (
      <Gallery
        onBack={() => setGalleryMode(false)}
        onDeleted={(ids) =>
          setMessages((prev) =>
            prev.map((m) =>
              m.role === 'ai' && m.generationId && ids.includes(m.generationId)
                ? { ...m, status: 'note', imageUrl: undefined, text: `#${m.generationId} 결과는 갤러리에서 삭제했습니다.` }
                : m,
            ),
          )
        }
      />
    ) :
    <div className="flex h-screen w-screen bg-[#0b0d14] text-neutral-100">
      <aside className="w-80 shrink-0 border-r border-neutral-800/80 bg-neutral-950/60 overflow-y-auto p-4 space-y-3">
        <div className="flex items-center gap-3 px-1 pb-2">
          <div className="w-9 h-9 rounded-xl grid place-items-center text-lg shadow-lg shadow-fuchsia-500/20" style={{ backgroundImage: 'linear-gradient(135deg, var(--accent-from), var(--accent-to))' }}>✦</div>
          <div>
            <p className="text-sm font-semibold leading-tight">Image AI Studio</p>
            <p className="text-[11px] text-neutral-500 leading-tight">그누빌 AI Lab · V100</p>
          </div>
        </div>

        <section className="card space-y-3">
          <h3 className="section-title">모델</h3>
        <div className="space-y-1">
          <label className="text-xs text-neutral-400">Checkpoint</label>
          <select
            className="field"
            value={checkpoint}
            onChange={(e) => setCheckpoint(e.target.value)}
          >
            {checkpoints.length === 0 && <option value="">모델 없음</option>}
            {checkpoints.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          {checkpointError && <p className="text-xs text-red-400">{checkpointError}</p>}
        </div>

        <div className="space-y-1">
          <label className="text-xs text-neutral-400">VAE (비워두면 체크포인트 기본값)</label>
          <select
            className="field"
            value={vae}
            onChange={(e) => setVae(e.target.value)}
          >
            <option value="">체크포인트 기본값</option>
            {availableVaes.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-neutral-400">LoRA</label>
          <select
            className="field"
            value=""
            onChange={(e) => addLora(e.target.value)}
          >
            <option value="">
              {availableLoras.length === 0 ? 'LoRA 없음 (models/loras에 파일 추가)' : '+ LoRA 추가'}
            </option>
            {availableLoras
              .filter((l) => !selectedLoras.some((s) => s.name === l))
              .map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
          </select>
          {selectedLoras.length > 0 && (
            <div className="space-y-1.5 pt-1">
              {selectedLoras.map((l) => (
                <div key={l.name} className="space-y-1">
                <div className="flex items-center gap-2 bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1.5">
                  <span className="text-xs text-neutral-300 truncate flex-1" title={l.name}>
                    {l.name}
                  </span>
                  <input
                    type="range"
                    min={-2}
                    max={2}
                    step={0.05}
                    className="w-20"
                    value={l.weight}
                    onChange={(e) => updateLoraWeight(l.name, Number(e.target.value))}
                  />
                  <span className="text-xs text-neutral-400 w-9 text-right">{l.weight.toFixed(2)}</span>
                  <button
                    className="text-xs text-neutral-500 hover:text-neutral-300"
                    title="LoRA 정보 (학습 태그, 트리거 후보)"
                    onClick={() => toggleLoraInfo(l.name)}
                  >
                    ⓘ
                  </button>
                  <button
                    className="text-xs text-neutral-500 hover:text-neutral-300"
                    onClick={() => removeLora(l.name)}
                  >
                    ✕
                  </button>
                </div>
                {loraInfoOpen === l.name && (
                  <div className="bg-neutral-950 border border-neutral-800 rounded-md px-2 py-2 text-xs space-y-1.5">
                    {(() => {
                      const info = loraInfos[l.name]
                      if (!info || info === 'loading') return <p className="text-neutral-500">불러오는 중...</p>
                      if (info === 'error' || !info.found || info.readable === false)
                        return <p className="text-neutral-500">정보를 읽을 수 없습니다.</p>
                      return (
                        <>
                          <p className="text-neutral-400">
                            {[info.title, info.base_model, info.network_dim ? `dim ${info.network_dim}` : ''].filter(Boolean).join(' · ') || '메타데이터 없음'}
                          </p>
                          {!info.has_tag_info ? (
                            <p className="text-neutral-500">이 LoRA 파일에는 학습 태그 정보가 없습니다.</p>
                          ) : (
                            <>
                              {info.trigger_candidates && info.trigger_candidates.length > 0 && (
                                <div className="flex flex-wrap gap-1 items-center">
                                  <span className="text-neutral-500">트리거 후보(클릭하면 프롬프트에 추가):</span>
                                  {info.trigger_candidates.map((t) => (
                                    <button
                                      key={t}
                                      className="px-1.5 py-0.5 rounded bg-violet-600/30 text-violet-200 hover:bg-violet-600/50"
                                      onClick={() => addToPrompt(t)}
                                    >
                                      {t}
                                    </button>
                                  ))}
                                </div>
                              )}
                              <div className="flex flex-wrap gap-1 items-center">
                                <span className="text-neutral-500">자주 쓰인 학습 태그:</span>
                                {info.top_tags?.slice(0, 12).map((t) => (
                                  <button
                                    key={t.tag}
                                    className="px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
                                    title={`${t.count}회`}
                                    onClick={() => addToPrompt(t.tag)}
                                  >
                                    {t.tag}
                                  </button>
                                ))}
                              </div>
                              <p className="text-neutral-600">실제 트리거 단어는 파일에 저장돼 있지 않아 후보일 뿐입니다.</p>
                            </>
                          )}
                        </>
                      )
                    })()}
                  </div>
                )}
                </div>
              ))}
            </div>
          )}
        </div>
        </section>

        <section className="card space-y-3">
          <h3 className="section-title">프롬프트</h3>
        <div className="space-y-1">
          <label className="text-xs text-neutral-400">Prompt Prefix (자동으로 앞에 붙음)</label>
          <TagSuggestField
            multiline
            className="w-full bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1.5 text-sm h-14 resize-none"
            value={promptPrefix}
            onChange={setPromptPrefix}
            placeholder="masterpiece, best quality..."
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs text-neutral-400">Negative Prompt</label>
          <TagSuggestField
            multiline
            className="w-full bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1.5 text-sm h-20 resize-none"
            value={negativePrompt}
            onChange={setNegativePrompt}
            placeholder="low quality, blurry..."
          />
        </div>
        </section>

        <section className="card">
          <h3 className="section-title">생성 옵션</h3>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <label className="text-xs text-neutral-400">Seed (빈칸=랜덤)</label>
            <input
              className="field"
              value={seed}
              onChange={(e) => setSeed(e.target.value)}
              placeholder="random"
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-neutral-400">Sampler</label>
            <select
              className="field"
              value={sampler}
              onChange={(e) => setSampler(e.target.value)}
            >
              {SAMPLERS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1 col-span-2">
            <label className="text-xs text-neutral-400">Prediction Type</label>
            <select
              className="field"
              value={sampling}
              onChange={(e) => setSampling(e.target.value)}
            >
              <option value="eps">eps (일반 모델)</option>
              <option value="v_prediction">v_prediction (V-Pred 모델)</option>
            </select>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-neutral-400">Steps</label>
            <input
              type="number"
              className="field"
              value={steps}
              onChange={(e) => setSteps(Number(e.target.value))}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-neutral-400">CFG</label>
            <input
              type="number"
              step="0.5"
              className="field"
              value={cfg}
              onChange={(e) => setCfg(Number(e.target.value))}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-neutral-400">Width</label>
            {sourceImage ? (
              <div className="field text-neutral-500">
                원본 크기 사용
              </div>
            ) : (
              <input
                type="number"
                className="field"
                value={width}
                onChange={(e) => setWidth(Number(e.target.value))}
              />
            )}
          </div>
          <div className="space-y-1">
            <label className="text-xs text-neutral-400">Height</label>
            {sourceImage ? (
              <div className="field text-neutral-500">
                원본 크기 사용
              </div>
            ) : (
              <input
                type="number"
                className="field"
                value={height}
                onChange={(e) => setHeight(Number(e.target.value))}
              />
            )}
          </div>
          <div className="space-y-1 col-span-2">
            <label className="text-xs text-neutral-400">
              Denoising Strength — {denoise.toFixed(2)}
              {maskBlob ? ' (인페인팅은 0.8~0.9 권장)' : sourceImage ? ' (부분 변형이면 낮게)' : ' (이미지 편집 시 적용)'}
            </label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              className="w-full"
              value={denoise}
              onChange={(e) => setDenoise(Number(e.target.value))}
            />
          </div>
          {maskBlob && (
            <div className="space-y-1 col-span-2">
              <label className="text-xs text-neutral-400">영역 처리 방식</label>
              <select
                className="field field-sm"
                value={inpaintMode}
                onChange={(e) => setInpaintMode(e.target.value as 'auto' | 'keep' | 'fill')}
              >
                <option value="auto">자동 (빈 영역·넓은 영역은 교체, 작은 세부는 유지)</option>
                <option value="keep">유지 (손·표정 등 세부 보정)</option>
                <option value="fill">완전 교체 (하늘·배경 등 통째로 바꾸기)</option>
              </select>
              <label className="text-xs text-neutral-400">주변 문맥 사용 (마스크 주변을 확대해서 다시 그림)</label>
              <select
                className="field field-sm"
                value={contextCrop}
                onChange={(e) => setContextCrop(e.target.value as 'auto' | 'always' | 'off')}
              >
                <option value="auto">자동 (작은 영역일 때만)</option>
                <option value="always">항상</option>
                <option value="off">끄기 (이미지 전체로 처리)</option>
              </select>
              <label className="flex items-center gap-2 text-xs text-neutral-400">
                <input type="checkbox" checked={harmonize} onChange={(e) => setHarmonize(e.target.checked)} />
                하모나이즈 (합성 후 경계 색감·조명 맞추기, 주변 문맥 사용 시)
              </label>
              <label className="text-xs text-neutral-400">마스크 경계 부드럽게 — {maskBlur}</label>
              <input type="range" min={0} max={31} step={1} className="w-full" value={maskBlur}
                onChange={(e) => setMaskBlur(Number(e.target.value))} />
            </div>
          )}
          <div className="space-y-1 col-span-2">
            <label className="flex items-center gap-2 text-xs text-neutral-400">
              <input type="checkbox" checked={hiresFix} onChange={(e) => setHiresFix(e.target.checked)} />
              Hires Fix (고해상도 2단계 생성)
            </label>
            {hiresFix && (
              <div className="grid grid-cols-2 gap-2 pt-1">
                <label className="text-xs text-neutral-400 col-span-2">
                  배율 {hiresScale.toFixed(2)}x
                  <input
                    type="range"
                    min={1.0}
                    max={2.0}
                    step={0.1}
                    className="w-full"
                    value={hiresScale}
                    onChange={(e) => setHiresScale(Number(e.target.value))}
                  />
                </label>
                <label className="text-xs text-neutral-400 col-span-2">
                  2차 Denoise {hiresDenoise.toFixed(2)}
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    className="w-full"
                    value={hiresDenoise}
                    onChange={(e) => setHiresDenoise(Number(e.target.value))}
                  />
                </label>
              </div>
            )}
          </div>
        </div>
        </section>

        <div className="card">
          <button
            className="section-toggle"
            onClick={() => setCharacterOpen((v) => !v)}
          >
            <span>캐릭터{characters.length > 0 ? ` (${characters.length})` : ''}</span>
            <Chevron open={characterOpen} />
          </button>
          {characterOpen && (
            <CharacterPanel
              characters={characters}
              activeIds={activeCharacterIds}
              onToggle={(id) => setActiveCharacterIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))}
              onChanged={refreshCharacters}
              availableLoras={availableLoras}
            />
          )}
        </div>

        <div className="card">
          <button
            className="section-toggle"
            onClick={() => setQwenOpen((v) => !v)}
          >
            <span>Qwen-Image</span>
            <Chevron open={qwenOpen} />
          </button>
          {qwenOpen && (
            <div className="mt-2 space-y-2">
              <label className="flex items-center gap-2 text-xs text-neutral-400">
                <input
                  type="checkbox"
                  checked={qwenEnabled}
                  disabled={!qwenAvailable.t2i && !qwenAvailable.edit}
                  onChange={(e) => {
                    setQwenEnabled(e.target.checked)
                    if (e.target.checked) {
                      setControlnetEnabled(false)
                      setIpEnabled(false)
                    }
                  }}
                />
                Qwen-Image 사용 (체크포인트 대신 Qwen-Image GGUF로 생성; 이미지 첨부 시 Qwen-Image-Edit로 수정)
              </label>
              {!qwenAvailable.t2i && !qwenAvailable.edit && (
                <p className="text-xs text-neutral-500">
                  모델이 없습니다. models/diffusion_models에 qwen-image*.gguf, models/text_encoders에 qwen_2.5_vl_7b*.safetensors,
                  models/vae에 qwen_image_vae.safetensors를 넣고 ComfyUI를 재시작하세요.
                </p>
              )}
              {qwenEnabled && qwenAvailable.t2i && !qwenAvailable.edit && (
                <p className="text-xs text-neutral-500">편집 모델(Qwen-Image-Edit)이 없어 이미지 첨부 생성은 지원되지 않습니다.</p>
              )}
              {qwenEnabled && !qwenAvailable.t2i && qwenAvailable.edit && (
                <p className="text-xs text-neutral-500">텍스트→이미지 모델이 없어 이미지를 첨부해야 생성할 수 있습니다.</p>
              )}
              {qwenEnabled && (
                <>
                  <select
                    className="field field-sm"
                    value=""
                    onChange={(e) => addQwenLora(e.target.value)}
                  >
                    <option value="">
                      {availableQwenLoras.length === 0 ? 'Qwen LoRA 없음 (models/loras에 파일 추가)' : '+ Qwen LoRA 추가'}
                    </option>
                    {availableQwenLoras
                      .filter((l) => !qwenSelectedLoras.some((s) => s.name === l))
                      .map((l) => (
                        <option key={l} value={l}>
                          {l}
                        </option>
                      ))}
                  </select>
                  {qwenSelectedLoras.length > 0 && (
                    <div className="space-y-1.5">
                      {qwenSelectedLoras.map((l) => (
                        <div key={l.name} className="flex items-center gap-2 bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1.5">
                          <span className="text-xs text-neutral-300 truncate flex-1" title={l.name}>
                            {l.name}
                          </span>
                          <input
                            type="range"
                            min={-2}
                            max={2}
                            step={0.05}
                            className="w-20"
                            value={l.weight}
                            onChange={(e) => updateQwenLoraWeight(l.name, Number(e.target.value))}
                          />
                          <span className="text-xs text-neutral-400 w-9 text-right">{l.weight.toFixed(2)}</span>
                          <button className="text-xs text-neutral-500 hover:text-neutral-300" onClick={() => removeQwenLora(l.name)}>
                            ✕
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        <div className="card">
          <button
            className="section-toggle"
            onClick={() => setControlnetOpen((v) => !v)}
          >
            <span>ControlNet</span>
            <Chevron open={controlnetOpen} />
          </button>
          {controlnetOpen && (
            <div className="mt-2 space-y-2">
              <label className="flex items-center gap-2 text-xs text-neutral-400">
                <input
                  type="checkbox"
                  checked={controlnetEnabled}
                  onChange={(e) => {
                    setControlnetEnabled(e.target.checked)
                    if (e.target.checked) {
                      setIpEnabled(false)
                      setQwenEnabled(false)
                    }
                  }}
                />
                ControlNet 사용 (활성화 시 다른 이미지 편집 모드 대신 이걸로 생성)
              </label>

              {controlnetEnabled && (
                <>
                  <input
                    ref={controlnetFileInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={onControlnetFileSelected}
                  />
                  <button
                    className="w-full text-left text-xs bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1.5 text-neutral-300"
                    onClick={pickControlnetFile}
                  >
                    {controlnetImage ? '📎 컨트롤 이미지 변경' : '📎 컨트롤 이미지 선택'}
                  </button>

                  {controlnetImage && (
                    <div className="flex gap-2">
                      <img
                        src={controlnetImage.previewUrl}
                        alt="control"
                        className="w-16 h-16 object-cover rounded border border-neutral-700"
                      />
                      {cannyPreviewUrl && (
                        <img
                          src={cannyPreviewUrl}
                          alt="canny preview"
                          className="w-16 h-16 object-cover rounded border border-neutral-700"
                        />
                      )}
                    </div>
                  )}

                  <select
                    className="field field-sm"
                    value={controlnetModel}
                    onChange={(e) => {
                      setControlnetModel(e.target.value)
                      setControlnetPreprocessor(/pose/i.test(e.target.value) ? 'openpose' : 'canny')
                      setCannyPreviewUrl(null)
                    }}
                  >
                    {availableControlnets.length === 0 && (
                      <option value="">ControlNet 모델 없음 (models/controlnet에 추가)</option>
                    )}
                    {availableControlnets.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>

                  <select
                    className="field field-sm"
                    value={controlnetPreprocessor}
                    onChange={(e) => {
                      setControlnetPreprocessor(e.target.value as 'none' | 'canny' | 'openpose')
                      setCannyPreviewUrl(null)
                    }}
                  >
                    <option value="canny">canny (엣지 추출)</option>
                    <option value="openpose">openpose (포즈만 추출, 전신 이미지 권장)</option>
                    <option value="none">none (업로드 이미지 그대로)</option>
                  </select>

                  {controlnetPreprocessor === 'openpose' && (
                    <button
                      className="w-full text-xs bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1 text-neutral-300 disabled:opacity-40"
                      onClick={handleCannyPreview}
                      disabled={!controlnetImage || cannyPreviewLoading}
                    >
                      {cannyPreviewLoading ? '포즈 추출 중...' : '포즈 뼈대 미리보기'}
                    </button>
                  )}

                  {controlnetPreprocessor === 'canny' && (
                    <div className="grid grid-cols-2 gap-2">
                      <label className="text-xs text-neutral-400">
                        Low {cannyLow.toFixed(2)}
                        <input
                          type="range"
                          min={0.01}
                          max={0.99}
                          step={0.01}
                          className="w-full"
                          value={cannyLow}
                          onChange={(e) => setCannyLow(Number(e.target.value))}
                        />
                      </label>
                      <label className="text-xs text-neutral-400">
                        High {cannyHigh.toFixed(2)}
                        <input
                          type="range"
                          min={0.01}
                          max={0.99}
                          step={0.01}
                          className="w-full"
                          value={cannyHigh}
                          onChange={(e) => setCannyHigh(Number(e.target.value))}
                        />
                      </label>
                      <button
                        className="col-span-2 text-xs bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1 text-neutral-300 disabled:opacity-40"
                        onClick={handleCannyPreview}
                        disabled={!controlnetImage || cannyPreviewLoading}
                      >
                        {cannyPreviewLoading ? '전처리 중...' : '엣지 미리보기'}
                      </button>
                    </div>
                  )}

                  <label className="text-xs text-neutral-400 block">
                    Strength {cnStrength.toFixed(2)}
                    <input
                      type="range"
                      min={0}
                      max={2}
                      step={0.05}
                      className="w-full"
                      value={cnStrength}
                      onChange={(e) => setCnStrength(Number(e.target.value))}
                    />
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="text-xs text-neutral-400">
                      Start {cnStart.toFixed(2)}
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.05}
                        className="w-full"
                        value={cnStart}
                        onChange={(e) => setCnStart(Number(e.target.value))}
                      />
                    </label>
                    <label className="text-xs text-neutral-400">
                      End {cnEnd.toFixed(2)}
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.05}
                        className="w-full"
                        value={cnEnd}
                        onChange={(e) => setCnEnd(Number(e.target.value))}
                      />
                    </label>
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        <div className="card">
          <button
            className="section-toggle"
            onClick={() => setIpOpen((v) => !v)}
          >
            <span>IP-Adapter</span>
            <Chevron open={ipOpen} />
          </button>
          {ipOpen && (
            <div className="mt-2 space-y-2">
              <label className="flex items-center gap-2 text-xs text-neutral-400">
                <input
                  type="checkbox"
                  checked={ipEnabled}
                  onChange={(e) => {
                    setIpEnabled(e.target.checked)
                    if (e.target.checked) {
                      setControlnetEnabled(false)
                      setQwenEnabled(false)
                    }
                  }}
                />
                IP-Adapter 사용 (레퍼런스 이미지의 캐릭터/스타일 참고)
              </label>
              {ipEnabled && (
                <>
                  <input ref={ipFileInputRef} type="file" accept="image/*" className="hidden" onChange={onIpFileSelected} />
                  <button
                    className="w-full text-left text-xs bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1.5 text-neutral-300"
                    onClick={() => ipFileInputRef.current?.click()}
                  >
                    {ipImage ? '📎 레퍼런스 이미지 변경' : '📎 레퍼런스 이미지 선택'}
                  </button>
                  {ipImage && (
                    <img src={ipImage.previewUrl} alt="reference" className="w-16 h-16 object-cover rounded border border-neutral-700" />
                  )}
                  <select
                    className="field field-sm"
                    value={ipModel}
                    onChange={(e) => setIpModel(e.target.value)}
                  >
                    {availableIpadapters.length === 0 && <option value="">IP-Adapter 모델 없음 (models/ipadapter)</option>}
                    {availableIpadapters.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                  <select
                    className="field field-sm"
                    value={clipVisionModel}
                    onChange={(e) => setClipVisionModel(e.target.value)}
                  >
                    {availableClipVisions.length === 0 && <option value="">CLIP Vision 모델 없음 (models/clip_vision)</option>}
                    {availableClipVisions.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                  <label className="text-xs text-neutral-400 block">
                    Weight {ipWeight.toFixed(2)} (V-Pred 모델은 0.15~0.25 권장, 높이면 화풍 깨짐)
                    <input type="range" min={0} max={1.5} step={0.05} className="w-full" value={ipWeight}
                      onChange={(e) => setIpWeight(Number(e.target.value))} />
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="text-xs text-neutral-400">
                      Start {ipStart.toFixed(2)}
                      <input type="range" min={0} max={1} step={0.05} className="w-full" value={ipStart}
                        onChange={(e) => setIpStart(Number(e.target.value))} />
                    </label>
                    <label className="text-xs text-neutral-400">
                      End {ipEnd.toFixed(2)}
                      <input type="range" min={0} max={1} step={0.05} className="w-full" value={ipEnd}
                        onChange={(e) => setIpEnd(Number(e.target.value))} />
                    </label>
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        <div className="card space-y-2">
          <label className="flex items-center gap-2 text-xs text-neutral-400">
            <input type="checkbox" checked={faceDetailer} onChange={(e) => setFaceDetailer(e.target.checked)} />
            Face/Hand Detailer (감지된 영역 자동 재보정)
          </label>
          {faceDetailer && (
            <>
              {availableBbox.length === 0 && (
                <p className="text-xs text-neutral-500">감지 모델 없음 (models/ultralytics/bbox)</p>
              )}
              {availableBbox.map((m) => (
                <label key={m} className="flex items-center gap-2 text-xs text-neutral-300">
                  <input
                    type="checkbox"
                    checked={faceModels.includes(m)}
                    onChange={(e) =>
                      setFaceModels((prev) => (e.target.checked ? [...prev, m] : prev.filter((x) => x !== m)))
                    }
                  />
                  {m.replace('bbox/', '')}
                </label>
              ))}
              <p className="text-xs text-neutral-500">여러 개를 체크하면 순서대로 이어서 보정합니다 (예: 얼굴 → 손)</p>
              <label className="text-xs text-neutral-400 block">
                보정 강도(denoise) {faceDenoise.toFixed(2)}
                <input type="range" min={0.1} max={0.8} step={0.05} className="w-full" value={faceDenoise}
                  onChange={(e) => setFaceDenoise(Number(e.target.value))} />
              </label>
            </>
          )}
        </div>

        <div className="card">
          <button
            className="section-toggle"
            onClick={toggleHistory}
          >
            <span>히스토리</span>
            <Chevron open={historyOpen} />
          </button>
          {historyOpen && (
            <div className="mt-2 space-y-1 max-h-64 overflow-y-auto">
              {historyLoading && <p className="text-xs text-neutral-500">불러오는 중...</p>}
              {!historyLoading && history.length === 0 && (
                <p className="text-xs text-neutral-500">기록이 없습니다.</p>
              )}
              {history.map((h) => (
                <button
                  key={h.id}
                  className="w-full flex items-center gap-2 p-1 rounded hover:bg-neutral-900 text-left"
                  onClick={() => h.image_path && !isVideoUrl(h.image_path) && continueEditing(h.id, h.image_path)}
                >
                  {h.image_path && isVideoUrl(h.image_path) && (
                    <video src={h.image_path} muted className="w-10 h-10 object-cover rounded" />
                  )}
                  {h.image_path && !isVideoUrl(h.image_path) && (
                    <img src={h.image_path} alt="" className="w-10 h-10 object-cover rounded" />
                  )}
                  <span className="text-xs text-neutral-400 truncate">
                    #{h.id} {h.prompt.slice(0, 30)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </aside>

      <main className="flex-1 flex flex-col min-w-0 relative">
        <div
          className="pointer-events-none absolute inset-x-0 top-0 h-72 opacity-60"
          style={{ background: 'radial-gradient(60% 100% at 50% 0%, rgba(139,92,246,0.18), transparent 70%)' }}
        />
        <header className="relative px-6 py-3 border-b border-neutral-800/80 bg-neutral-950/40 backdrop-blur">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-base font-semibold tracking-tight">
              <span className="accent-text">Studio</span>
            </h1>
            <span
              className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border ${
                gpu ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300' : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
              }`}
              title={gpu ? 'ComfyUI가 실행 중입니다' : '포털에서 ComfyUI를 켜야 생성할 수 있습니다'}
            >
              <span className="relative flex w-1.5 h-1.5">{gpu && <span className="absolute inset-0 rounded-full bg-emerald-400 animate-ping" />}<span className={`relative w-1.5 h-1.5 rounded-full ${gpu ? 'bg-emerald-400' : 'bg-amber-400'}`} /></span>
              {gpu ? 'ComfyUI 연결됨' : 'ComfyUI 꺼짐'}
            </span>
            {gpu && (
              <div className="flex items-center gap-2 text-xs text-neutral-400" title={gpu.name}>
                <span>VRAM</span>
                <div className="w-28 h-1.5 rounded-full bg-neutral-800 overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.min(100, (gpu.vram_used_mb / Math.max(1, gpu.vram_total_mb)) * 100)}%`,
                      backgroundImage: 'linear-gradient(90deg, var(--accent-from), var(--accent-to))',
                    }}
                  />
                </div>
                <span className="tabular-nums">
                  {(gpu.vram_used_mb / 1024).toFixed(1)} / {(gpu.vram_total_mb / 1024).toFixed(0)} GB
                </span>
              </div>
            )}
            <button className="ml-auto chip" onClick={() => setGalleryMode(true)}>
              🖼 갤러리
            </button>
            <button className="chip" onClick={() => setDashboardMode(true)}>
              API 요청 대시보드
            </button>
          </div>
        </header>

        <div ref={scrollRef} className="relative flex-1 overflow-y-auto px-6 py-6">
          <div className="max-w-3xl mx-auto space-y-5">
          {/* Hero stays on top; generated cards stack below it. It gets more compact once the chat starts. */}
          {(() => {
            const compact = messages.length > 0
            return (
            <div className={`relative flex flex-col items-center text-center anim-in transition-all duration-500 ${compact ? 'pt-4 pb-2' : 'pt-16 pb-6'}`}>
              <span className="orb w-72 h-72 -top-10 left-[10%]" style={{ background: '#8b5cf6' }} />
              <span className="orb w-64 h-64 top-24 right-[8%]" style={{ background: '#d946ef', animationDelay: '-5s', ['--fx' as string]: '-40px', ['--fy' as string]: '25px' } as CSSProperties} />
              <span className="orb w-56 h-56 top-56 left-[35%]" style={{ background: '#38bdf8', opacity: 0.2, animationDelay: '-9s' }} />
              <div
                className={`relative rounded-2xl grid place-items-center logo-glow transition-all duration-500 ${compact ? 'w-10 h-10 text-lg mb-3' : 'w-14 h-14 text-2xl mb-5'}`}
                style={{ backgroundImage: 'linear-gradient(135deg, var(--accent-from), var(--accent-to))' }}
              >
                ✦
              </div>
              <h2 className={`relative font-semibold tracking-tight transition-all duration-500 ${compact ? 'text-xl' : 'text-3xl'}`}>무엇을 <span className="accent-text">그려 볼까요?</span></h2>
              <p className="relative mt-2 text-sm text-neutral-400 max-w-md">
                태그를 쉼표로 이어 적으면 됩니다. 이미지를 첨부하면 편집하고, 결과에서 바로 영상으로 이어 만들 수 있어요.
              </p>
              {!gpu && (
                <div className="mt-6 w-full max-w-md text-left rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
                  ComfyUI가 꺼져 있어요. <a className="underline underline-offset-2 hover:text-amber-100" href="https://192.168.50.14" target="_blank" rel="noreferrer">포털</a>에서 ComfyUI를 켠 뒤 생성하세요.
                </div>
              )}
              <div className={`flex flex-wrap justify-center gap-2 max-w-2xl ${compact ? 'mt-4' : 'mt-8'}`}>
                {EXAMPLE_PROMPTS.map((p, i) => (
                  <button key={p} className="chip anim-in" style={{ animationDelay: `${200 + i * 80}ms` }} onClick={() => setPrompt(p)}>
                    {p}
                  </button>
                ))}
              </div>
            </div>
            )
          })()}
          {messages.map((m) =>
            m.role === 'user' ? (
              <div key={m.id} className="flex justify-end anim-in">
                <div className="max-w-xl rounded-2xl rounded-br-md px-4 py-2.5 text-sm space-y-2 text-white shadow-lg shadow-violet-900/30" style={{ backgroundImage: 'linear-gradient(135deg, #6d28d9, #a21caf)' }}>
                  {m.sourceImageUrl && (
                    <img src={m.sourceImageUrl} alt="원본" className="rounded-lg max-h-40 ring-1 ring-white/20" />
                  )}
                  {m.text}
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex justify-start anim-in">
                <div className="max-w-xl w-fit card rounded-2xl rounded-bl-md px-4 py-3 text-sm space-y-3">
                  {m.note && <p className="text-xs text-neutral-400 whitespace-pre-line">{m.note}</p>}
                  {m.status === 'note' && <p className="text-neutral-300 whitespace-pre-line">{m.text}</p>}
                  {(m.status === 'queued' || m.status === 'running') && (() => {
                    const pct = genPercent(m)
                    const label =
                      m.status === 'queued'
                        ? m.ahead && m.ahead > 0
                          ? `대기열 ${m.ahead + 1}번째 (앞에 ${m.ahead}개 작업)`
                          : '대기 중...'
                        : `${m.segment ? `[구간 ${m.segment[0]}/${m.segment[1]}] ` : ''}${m.stage ?? '준비 중'}`
                    return (
                      <div className="space-y-2.5 w-80 max-w-full">
                        <div
                          className="gen-canvas"
                          style={{ aspectRatio: String(m.aspect ?? 1), ['--noise' as string]: String(Math.max(0.15, 0.8 - pct / 140)) } as CSSProperties}
                        >
                          <span className="shine" />
                          <div className="absolute inset-0 grid place-items-center text-center px-4">
                            <div>
                              <p className="text-4xl font-semibold tabular-nums text-white/90 drop-shadow">
                                {m.status === 'running' && pct > 0 ? `${pct}%` : '✦'}
                              </p>
                              <p className="mt-1.5 text-xs text-white/75 breathe">{label}</p>
                            </div>
                          </div>
                        </div>
                        <div className="progress-track">
                          <div className="progress-fill" style={{ width: `${m.status === 'running' ? Math.max(4, pct) : 2}%` }} />
                        </div>
                        {m.status === 'running' && (
                          <div className="flex items-center justify-between gap-2">
                            <p className="text-xs text-neutral-500">
                              {m.progress && m.progress.max > 0 ? `${m.progress.value}/${m.progress.max} 스텝` : '샘플링 시작 전'}
                              {m.startedAt !== undefined && ` · 경과 ${formatDuration((clock - m.startedAt) / 1000)}`}
                              {m.progress?.eta != null && ` · 남은 약 ${formatDuration(m.progress.eta)}`}
                            </p>
                            <button className="text-xs text-red-400 hover:text-red-300 shrink-0" onClick={() => handleCancel(m.generationId)}>
                              ✕ 취소
                            </button>
                          </div>
                        )}
                      </div>
                    )
                  })()}
                  {m.status === 'cancelled' && <p className="text-neutral-500">취소됨</p>}
                  {m.status === 'error' && <p className="text-red-400">오류: {m.text}</p>}
                  {m.status === 'done' && m.imageUrl && isVideoUrl(m.imageUrl) && (
                    <>
                      <video src={m.imageUrl} controls loop muted autoPlay playsInline className="reveal rounded-xl max-w-full max-h-[70vh] shadow-2xl shadow-black/40 ring-1 ring-white/5" />
                      {m.generationId && interpAvailable && (
                        <button className="chip" onClick={() => smoothVideo(m.generationId!)}>
                          🎞 부드럽게 (프레임 2배, 약 10초)
                        </button>
                      )}
                    </>
                  )}
                  {m.status === 'done' && m.imageUrl && !isVideoUrl(m.imageUrl) && (
                    <>
                      <a href={m.imageUrl} target="_blank" rel="noreferrer"><img src={m.imageUrl} alt="generated" className="reveal rounded-xl max-w-full shadow-2xl shadow-black/40 ring-1 ring-white/5" /></a>
                      <div className="flex flex-wrap gap-2">
                        <button
                          className="chip"
                          onClick={() => m.generationId && continueEditing(m.generationId, m.imageUrl!)}
                        >
                          🔁 이 이미지로 계속 편집
                        </button>
                        <button
                          className="chip"
                          onClick={() => m.generationId && startVideo(m.generationId, m.imageUrl!)}
                        >
                          🎬 영상으로 만들기
                        </button>
                        <button
                          className="chip"
                          onClick={() => m.generationId && saveAsCharacter(m.generationId)}
                        >
                          👤 캐릭터로 저장
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            ),
          )}
          </div>
        </div>

        <div className="relative px-6 pb-5 pt-2">
          <div className="max-w-3xl mx-auto space-y-2">
          {videoSource && (
            <div className="flex items-center gap-2 bg-neutral-900 border border-neutral-700 rounded-lg px-2 py-1.5 w-fit">
              <img src={videoSource.imageUrl} alt="video source" className="w-10 h-10 object-cover rounded" />
              <span className="text-xs text-neutral-400">🎬 이 이미지를 영상으로 (원하는 움직임을 입력)</span>
              <select
                className="bg-neutral-950 border border-neutral-700 rounded px-1 py-0.5 text-xs"
                value={videoQuality}
                onChange={(e) => setVideoQuality(e.target.value as 'auto' | 'fast' | 'high')}
                title="고화질: Wan 2.2 14B(GGUF) + 4스텝 가속. 빠름: 5B"
              >
                <option value="auto">품질 자동</option>
                <option value="fast">빠름 (5B)</option>
                <option value="high" disabled={!hqAvailable}>고화질 (14B)</option>
              </select>
              <select
                className="bg-neutral-950 border border-neutral-700 rounded px-1 py-0.5 text-xs"
                value={videoLength}
                onChange={(e) => setVideoLength(Number(e.target.value))}
              >
                {videoHq ? (
                  <>
                    <option value={33}>2초</option>
                    <option value={49}>3초</option>
                    <option value={81}>5초</option>
                  </>
                ) : (
                  <>
                    <option value={25}>1초</option>
                    <option value={49}>2초</option>
                  </>
                )}
              </select>
              {interpAvailable && (
                <select
                  className="bg-neutral-950 border border-neutral-700 rounded px-1 py-0.5 text-xs"
                  value={videoInterp}
                  onChange={(e) => setVideoInterp(Number(e.target.value))}
                  title="프레임 보간(RIFE): 프레임을 늘려 움직임을 부드럽게 합니다 (수 초 추가)"
                >
                  <option value={1}>보간 끄기</option>
                  <option value={2}>부드럽게 2배</option>
                </select>
              )}
              {videoHq && (
                <select
                  className="bg-neutral-950 border border-neutral-700 rounded px-1 py-0.5 text-xs"
                  value={videoSegments}
                  onChange={(e) => setVideoSegments(Number(e.target.value))}
                  title="이어 붙이기: 마지막 프레임에서 다음 구간을 이어 만듭니다. 구간마다 약 9분, 인물이 조금씩 변할 수 있습니다"
                >
                  <option value={1}>1구간</option>
                  <option value={2}>2구간 (약 10초)</option>
                  <option value={3}>3구간 (약 15초)</option>
                </select>
              )}
              {!videoHq && (
                <label className="text-xs text-neutral-500">
                  스텝{' '}
                  <input
                    type="number"
                    min={10}
                    max={30}
                    className="w-12 bg-neutral-950 border border-neutral-700 rounded px-1 py-0.5 text-xs"
                    value={videoSteps}
                    onChange={(e) => setVideoSteps(Math.max(10, Math.min(30, Number(e.target.value) || 20)))}
                  />
                </label>
              )}
              <button className="text-xs text-neutral-500 hover:text-neutral-300" onClick={() => setVideoSource(null)}>
                ✕
              </button>
            </div>
          )}
          {sourceImage && (
            <div className="flex items-center gap-2 bg-neutral-900 border border-neutral-700 rounded-lg px-2 py-1.5 w-fit">
              <img src={sourceImageUrl()!} alt="source" className="w-10 h-10 object-cover rounded" />
              <span className="text-xs text-neutral-400">
                {maskBlob ? '마스크 적용됨 (인페인팅)' : '이 이미지 편집 중'}
              </span>
              <button
                className="text-xs text-violet-400 hover:text-violet-300"
                onClick={() => setMaskEditorOpen(true)}
              >
                🖌 마스크로 편집
              </button>
              {maskBlob && (
                <button className="text-xs text-neutral-500 hover:text-neutral-300" onClick={() => setMaskBlob(null)}>
                  마스크 해제
                </button>
              )}
              <button className="text-xs text-neutral-500 hover:text-neutral-300" onClick={clearSource}>
                ✕
              </button>
            </div>
          )}
      {maskEditorOpen && sourceImage && (
            <MaskEditor
              imageUrl={sourceImageUrl()!}
              onCancel={() => setMaskEditorOpen(false)}
              onConfirm={(blob) => {
                setMaskBlob(blob)
                setMaskEditorOpen(false)
                setDenoise(0.85)
              }}
            />
          )}
          <div className="composer shadow-2xl shadow-black/40"><div className="composer-inner flex items-center gap-2 p-1.5">
            <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={onFileSelected} />
            <button
              className="shrink-0 w-10 h-10 flex items-center justify-center rounded-xl text-neutral-400 hover:text-neutral-100 hover:bg-neutral-800"
              onClick={pickFile}
              title="이미지 업로드"
            >
              📎
            </button>
            <TagSuggestField
              dropUp
              className="w-full bg-transparent border-0 px-2 py-2 text-sm focus:!shadow-none placeholder:text-neutral-500"
              placeholder={videoSource ? '영어로 움직임을 적어주세요. 예: The girl walks toward the camera, hair flowing' : sourceImage ? '이미지를 어떻게 바꿀까요?' : '이미지를 어떻게 만들까요?'}
              value={prompt}
              onChange={setPrompt}
              onEnter={handleGenerate}
            />
            <button
              className="btn-primary shrink-0 rounded-xl px-5 py-2.5 text-sm"
              onClick={handleGenerate}
              disabled={!prompt.trim() || !checkpoint}
            >
              생성
            </button>
          </div></div>
          </div>
        </div>
      </main>
    </div>
  )
}

export default App
