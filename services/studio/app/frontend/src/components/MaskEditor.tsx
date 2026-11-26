import { useEffect, useRef, useState } from 'react'

interface Point {
  x: number
  y: number
}

interface MaskEditorProps {
  imageUrl: string
  onCancel: () => void
  onConfirm: (maskBlob: Blob) => void
  /** Proposed mask (PNG data URL, white = repaint) drawn onto the canvas so the user can confirm or fix it. */
  initialMask?: string | null
  notice?: string
}

export default function MaskEditor({ imageUrl, onCancel, onConfirm, initialMask, notice }: MaskEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)
  const drawingRef = useRef(false)
  const lastPointRef = useRef<Point | null>(null)

  const [brushSize, setBrushSize] = useState(40)
  const [isEraser, setIsEraser] = useState(false)
  const [history, setHistory] = useState<string[]>([])
  const [redoStack, setRedoStack] = useState<string[]>([])
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const img = imgRef.current
    const canvas = canvasRef.current
    if (!img || !canvas) return

    const setup = () => {
      canvas.width = img.naturalWidth
      canvas.height = img.naturalHeight
      if (initialMask) {
        const m = new Image()
        m.onload = () => {
          const off = document.createElement('canvas')
          off.width = canvas.width
          off.height = canvas.height
          const octx = off.getContext('2d')!
          octx.drawImage(m, 0, 0, off.width, off.height)
          const px = octx.getImageData(0, 0, off.width, off.height)
          for (let i = 0; i < px.data.length; i += 4) {
            const on = px.data[i] > 127
            px.data[i] = 255
            px.data[i + 1] = 60
            px.data[i + 2] = 60
            px.data[i + 3] = on ? 166 : 0
          }
          canvas.getContext('2d')!.putImageData(px, 0, 0)
        }
        m.src = initialMask
      }
      setReady(true)
    }

    if (img.complete && img.naturalWidth > 0) setup()
    else img.onload = setup
  }, [imageUrl, initialMask])

  function getCanvasPoint(e: React.MouseEvent<HTMLCanvasElement>): Point {
    const canvas = canvasRef.current!
    const rect = canvas.getBoundingClientRect()
    const scaleX = canvas.width / rect.width
    const scaleY = canvas.height / rect.height
    return { x: (e.clientX - rect.left) * scaleX, y: (e.clientY - rect.top) * scaleY }
  }

  function pushHistory() {
    const canvas = canvasRef.current!
    setHistory((prev) => [...prev, canvas.toDataURL()])
    setRedoStack([])
  }

  function drawDot(ctx: CanvasRenderingContext2D, p: Point) {
    ctx.globalCompositeOperation = isEraser ? 'destination-out' : 'source-over'
    ctx.fillStyle = 'rgba(255, 60, 60, 0.65)'
    ctx.beginPath()
    ctx.arc(p.x, p.y, brushSize / 2, 0, Math.PI * 2)
    ctx.fill()
  }

  function drawLine(ctx: CanvasRenderingContext2D, from: Point, to: Point) {
    ctx.globalCompositeOperation = isEraser ? 'destination-out' : 'source-over'
    ctx.strokeStyle = 'rgba(255, 60, 60, 0.65)'
    ctx.lineWidth = brushSize
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.beginPath()
    ctx.moveTo(from.x, from.y)
    ctx.lineTo(to.x, to.y)
    ctx.stroke()
  }

  function handleMouseDown(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!ready) return
    pushHistory()
    const ctx = canvasRef.current!.getContext('2d')!
    const p = getCanvasPoint(e)
    drawDot(ctx, p)
    lastPointRef.current = p
    drawingRef.current = true
  }

  function handleMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!drawingRef.current) return
    const ctx = canvasRef.current!.getContext('2d')!
    const p = getCanvasPoint(e)
    if (lastPointRef.current) drawLine(ctx, lastPointRef.current, p)
    lastPointRef.current = p
  }

  function stopDrawing() {
    drawingRef.current = false
    lastPointRef.current = null
  }

  function restoreFrom(dataUrl: string) {
    const canvas = canvasRef.current!
    const ctx = canvas.getContext('2d')!
    const img = new Image()
    img.onload = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      ctx.globalCompositeOperation = 'source-over'
      ctx.drawImage(img, 0, 0)
    }
    img.src = dataUrl
  }

  function handleUndo() {
    if (history.length === 0) return
    const canvas = canvasRef.current!
    const current = canvas.toDataURL()
    const prev = history[history.length - 1]
    setHistory((h) => h.slice(0, -1))
    setRedoStack((r) => [...r, current])
    restoreFrom(prev)
  }

  function handleRedo() {
    if (redoStack.length === 0) return
    const canvas = canvasRef.current!
    const current = canvas.toDataURL()
    const next = redoStack[redoStack.length - 1]
    setRedoStack((r) => r.slice(0, -1))
    setHistory((h) => [...h, current])
    restoreFrom(next)
  }

  function handleClear() {
    pushHistory()
    const canvas = canvasRef.current!
    canvas.getContext('2d')!.clearRect(0, 0, canvas.width, canvas.height)
  }

  function handleConfirm() {
    const canvas = canvasRef.current!
    const w = canvas.width
    const h = canvas.height
    const ctx = canvas.getContext('2d')!
    const src = ctx.getImageData(0, 0, w, h)

    const out = document.createElement('canvas')
    out.width = w
    out.height = h
    const outCtx = out.getContext('2d')!
    const outData = outCtx.createImageData(w, h)

    for (let i = 0; i < src.data.length; i += 4) {
      const alpha = src.data[i + 3]
      const v = alpha > 10 ? 255 : 0
      outData.data[i] = v
      outData.data[i + 1] = v
      outData.data[i + 2] = v
      outData.data[i + 3] = 255
    }
    outCtx.putImageData(outData, 0, 0)

    out.toBlob((blob) => {
      if (blob) onConfirm(blob)
    }, 'image/png')
  }

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
      <div className="bg-neutral-950 border border-neutral-800 rounded-xl p-4 max-w-3xl w-full space-y-3">
        <h3 className="text-sm font-semibold text-neutral-200">마스크 그리기 (브러시로 칠한 영역만 바뀝니다)</h3>
        {notice && <p className="text-xs text-violet-300 whitespace-pre-line">{notice}</p>}

        <div className="relative inline-block max-w-full">
          {/* eslint-disable-next-line jsx-a11y/alt-text */}
          <img ref={imgRef} src={imageUrl} className="max-w-full max-h-[60vh] block select-none" draggable={false} />
          <canvas
            ref={canvasRef}
            className="absolute inset-0 w-full h-full cursor-crosshair"
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={stopDrawing}
            onMouseLeave={stopDrawing}
          />
        </div>

        <div className="flex flex-wrap items-center gap-3 text-sm">
          <label className="flex items-center gap-2 text-neutral-400">
            브러시 크기
            <input
              type="range"
              min={5}
              max={150}
              value={brushSize}
              onChange={(e) => setBrushSize(Number(e.target.value))}
            />
            <span className="text-neutral-300 w-8">{brushSize}</span>
          </label>

          <button
            className={`px-3 py-1.5 rounded-md border ${
              isEraser ? 'bg-violet-600 border-violet-500 text-white' : 'bg-neutral-900 border-neutral-700 text-neutral-300'
            }`}
            onClick={() => setIsEraser((v) => !v)}
          >
            🧹 지우개
          </button>

          <button
            className="px-3 py-1.5 rounded-md bg-neutral-900 border border-neutral-700 text-neutral-300 disabled:opacity-40"
            onClick={handleUndo}
            disabled={history.length === 0}
          >
            ↩ 되돌리기
          </button>
          <button
            className="px-3 py-1.5 rounded-md bg-neutral-900 border border-neutral-700 text-neutral-300 disabled:opacity-40"
            onClick={handleRedo}
            disabled={redoStack.length === 0}
          >
            ↪ 다시하기
          </button>
          <button
            className="px-3 py-1.5 rounded-md bg-neutral-900 border border-neutral-700 text-neutral-300"
            onClick={handleClear}
          >
            🗑 마스크 지우기
          </button>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button className="px-4 py-2 rounded-md bg-neutral-800 text-neutral-300 text-sm" onClick={onCancel}>
            취소
          </button>
          <button
            className="px-4 py-2 rounded-md bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium"
            onClick={handleConfirm}
          >
            마스크 확정
          </button>
        </div>
      </div>
    </div>
  )
}
