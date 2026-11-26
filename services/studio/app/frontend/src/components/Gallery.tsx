import { useCallback, useEffect, useMemo, useState } from 'react'
import { deleteGenerations, getGallery, type GalleryItem } from '../services/api'

const PAGE = 60
type Filter = 'all' | 'image' | 'video'

function formatBytes(n: number | null): string {
  if (n == null) return '파일 없음'
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(value: string): string {
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

function Thumb({ item }: { item: GalleryItem }) {
  if (item.file_missing) {
    return <div className="w-full h-full grid place-items-center text-xs text-neutral-500 bg-neutral-900">파일 없음</div>
  }
  return item.media_type === 'video' ? (
    <video
      src={item.image_path}
      muted
      loop
      playsInline
      preload="metadata"
      className="w-full h-full object-cover"
      onMouseEnter={(e) => void e.currentTarget.play().catch(() => undefined)}
      onMouseLeave={(e) => e.currentTarget.pause()}
    />
  ) : (
    <img src={item.image_path} alt="" loading="lazy" className="w-full h-full object-cover" />
  )
}

export default function Gallery({ onBack, onDeleted }: { onBack: () => void; onDeleted: (ids: number[]) => void }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [items, setItems] = useState<GalleryItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [viewIndex, setViewIndex] = useState<number | null>(null)
  const [confirmIds, setConfirmIds] = useState<number[] | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [toast, setToast] = useState('')

  const load = useCallback(async (reset: boolean, f: Filter, offset: number) => {
    setLoading(true)
    setError('')
    try {
      const res = await getGallery(reset ? 0 : offset, PAGE, f === 'all' ? undefined : f)
      setTotal(res.total)
      setItems((prev) => (reset ? res.items : [...prev, ...res.items]))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    setSelected(new Set())
    void load(true, filter, 0)
  }, [filter, load])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 3500)
    return () => clearTimeout(t)
  }, [toast])

  const viewing = viewIndex != null ? items[viewIndex] : null

  useEffect(() => {
    if (viewIndex == null || confirmIds) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setViewIndex(null)
      if (e.key === 'ArrowRight') setViewIndex((i) => (i == null ? i : Math.min(items.length - 1, i + 1)))
      if (e.key === 'ArrowLeft') setViewIndex((i) => (i == null ? i : Math.max(0, i - 1)))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [viewIndex, items.length, confirmIds])

  const totalSize = useMemo(() => items.reduce((s, i) => s + (i.file_size ?? 0), 0), [items])

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function runDelete(ids: number[]) {
    setDeleting(true)
    try {
      const res = await deleteGenerations(ids)
      const done = res.results.filter((r) => r.deleted).map((r) => r.id)
      const failed = res.results.filter((r) => !r.deleted)
      setItems((prev) => prev.filter((i) => !done.includes(i.id)))
      setTotal((t) => t - done.length)
      setSelected(new Set())
      setViewIndex(null)
      onDeleted(done)
      setToast(
        `${done.length}개 삭제 (파일 ${res.files}개)` + (failed.length ? ` · ${failed.length}개 실패: ${failed[0].reason}` : ''),
      )
    } catch (e) {
      setToast(`삭제 실패: ${(e as Error).message}`)
    } finally {
      setDeleting(false)
      setConfirmIds(null)
    }
  }

  return (
    <div className="flex flex-col h-screen w-screen bg-[#0b0d14] text-neutral-100">
      <header className="px-6 py-3 border-b border-neutral-800/80 bg-neutral-950/40 backdrop-blur flex items-center gap-3 flex-wrap">
        <button className="chip" onClick={onBack}>← 스튜디오</button>
        <h1 className="text-base font-semibold tracking-tight">
          <span className="accent-text">갤러리</span>
        </h1>
        <span className="text-xs text-neutral-500">
          {total}개 · 보이는 항목 {formatBytes(totalSize)}
        </span>
        <div className="flex rounded-lg border border-neutral-800 overflow-hidden text-xs ml-2">
          {(['all', 'image', 'video'] as Filter[]).map((f) => (
            <button
              key={f}
              className={`px-3 py-1.5 ${filter === f ? 'bg-violet-500/20 text-violet-200' : 'text-neutral-400 hover:text-neutral-200'}`}
              onClick={() => setFilter(f)}
            >
              {f === 'all' ? '전체' : f === 'image' ? '이미지' : '영상'}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {selecting && (
            <>
              <button className="chip" onClick={() => setSelected(new Set(items.map((i) => i.id)))}>
                전체 선택
              </button>
              <button
                className="text-xs px-3 py-1.5 rounded-full bg-red-500/15 border border-red-500/40 text-red-300 hover:bg-red-500/25 disabled:opacity-40"
                disabled={selected.size === 0}
                onClick={() => setConfirmIds([...selected])}
              >
                {selected.size}개 삭제
              </button>
            </>
          )}
          <button
            className={`chip ${selecting ? '!border-violet-500 !text-violet-200' : ''}`}
            onClick={() => {
              setSelecting((v) => !v)
              setSelected(new Set())
            }}
          >
            {selecting ? '선택 끝내기' : '선택'}
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {error && <p className="text-sm text-red-400 mb-4">{error}</p>}
        {!loading && items.length === 0 && !error && (
          <p className="text-center text-sm text-neutral-500 mt-20">아직 생성된 결과가 없습니다.</p>
        )}
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))' }}>
          {items.map((item, idx) => {
            const isSel = selected.has(item.id)
            return (
              <button
                key={item.id}
                style={{ animationDelay: `${(idx % 30) * 25}ms` }}
                className={`tile group relative aspect-square rounded-xl overflow-hidden border bg-neutral-900 text-left ${
                  isSel ? 'border-violet-400 ring-2 ring-violet-500/60' : 'border-neutral-800 hover:border-neutral-600'
                }`}
                onClick={() => (selecting ? toggle(item.id) : setViewIndex(idx))}
              >
                <Thumb item={item} />
                <div className="absolute inset-x-0 bottom-0 p-2 bg-gradient-to-t from-black/80 to-transparent opacity-0 group-hover:opacity-100 transition-opacity">
                  <p className="text-[11px] text-neutral-200 line-clamp-2">{item.prompt}</p>
                </div>
                <span className="absolute top-2 left-2 text-[10px] px-1.5 py-0.5 rounded bg-black/60 text-neutral-300">#{item.id}</span>
                {item.media_type === 'video' && (
                  <span className="absolute top-2 right-2 text-[10px] px-1.5 py-0.5 rounded bg-black/60 text-neutral-200">영상</span>
                )}
                {selecting && (
                  <span
                    className={`absolute bottom-2 right-2 w-5 h-5 rounded-md border grid place-items-center text-xs ${
                      isSel ? 'bg-violet-500 border-violet-400 text-white' : 'bg-black/50 border-neutral-500'
                    }`}
                  >
                    {isSel ? '✓' : ''}
                  </span>
                )}
              </button>
            )
          })}
        </div>
        {items.length < total && (
          <div className="flex justify-center mt-6">
            <button className="chip" disabled={loading} onClick={() => void load(false, filter, items.length)}>
              {loading ? '불러오는 중...' : `더 보기 (${total - items.length}개 남음)`}
            </button>
          </div>
        )}
      </div>

      {viewing && (
        <div className="fixed inset-0 z-40 bg-black/85 backdrop-blur-sm flex" onClick={() => setViewIndex(null)}>
          <div className="flex-1 flex items-center justify-center p-6 min-w-0" onClick={(e) => e.stopPropagation()}>
            {viewIndex! > 0 && (
              <button className="absolute left-4 chip" onClick={() => setViewIndex(viewIndex! - 1)}>‹</button>
            )}
            {viewing.media_type === 'video' ? (
              <video src={viewing.image_path} controls autoPlay loop className="max-w-full max-h-full rounded-xl" />
            ) : (
              <img key={viewing.id} src={viewing.image_path} alt="" className="reveal max-w-full max-h-full object-contain rounded-xl" />
            )}
            {viewIndex! < items.length - 1 && (
              <button className="absolute right-[22rem] chip" onClick={() => setViewIndex(viewIndex! + 1)}>›</button>
            )}
          </div>
          <aside className="w-80 shrink-0 bg-neutral-950 border-l border-neutral-800 p-5 overflow-y-auto space-y-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold">#{viewing.id}</p>
              <button className="text-neutral-500 hover:text-neutral-200" onClick={() => setViewIndex(null)}>✕</button>
            </div>
            <div>
              <p className="section-title">프롬프트</p>
              <p className="text-xs text-neutral-300 whitespace-pre-wrap break-words">{viewing.prompt}</p>
            </div>
            {viewing.negative_prompt && (
              <div>
                <p className="section-title">네거티브</p>
                <p className="text-xs text-neutral-400 whitespace-pre-wrap break-words">{viewing.negative_prompt}</p>
              </div>
            )}
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
              <dt className="text-neutral-500">모델</dt><dd className="text-neutral-300 truncate" title={viewing.model}>{viewing.model}</dd>
              <dt className="text-neutral-500">크기</dt><dd className="text-neutral-300">{viewing.width}×{viewing.height}</dd>
              <dt className="text-neutral-500">시드</dt><dd className="text-neutral-300">{viewing.seed}</dd>
              <dt className="text-neutral-500">스텝 / CFG</dt><dd className="text-neutral-300">{viewing.steps} / {viewing.cfg}</dd>
              <dt className="text-neutral-500">샘플러</dt><dd className="text-neutral-300">{viewing.sampler}</dd>
              <dt className="text-neutral-500">파일</dt><dd className="text-neutral-300">{formatBytes(viewing.file_size)}</dd>
              <dt className="text-neutral-500">생성</dt><dd className="text-neutral-300">{formatTime(viewing.created_at)}</dd>
            </dl>
            <div className="flex gap-2 pt-2">
              <a className="chip" href={viewing.image_path} download>
                다운로드
              </a>
              <button
                className="text-xs px-3 py-1.5 rounded-full bg-red-500/15 border border-red-500/40 text-red-300 hover:bg-red-500/25"
                onClick={() => setConfirmIds([viewing.id])}
              >
                삭제
              </button>
            </div>
          </aside>
        </div>
      )}

      {confirmIds && (
        <div className="fixed inset-0 z-50 bg-black/70 grid place-items-center p-4" onClick={() => !deleting && setConfirmIds(null)}>
          <div className="card w-full max-w-sm space-y-3" onClick={(e) => e.stopPropagation()}>
            <p className="text-sm font-semibold">{confirmIds.length}개를 삭제할까요?</p>
            <p className="text-xs text-neutral-400">
              Studio 결과 폴더와 ComfyUI 출력 폴더의 파일, 생성 기록까지 모두 지워집니다. 되돌릴 수 없습니다.
            </p>
            <div className="flex justify-end gap-2 pt-1">
              <button className="chip" disabled={deleting} onClick={() => setConfirmIds(null)}>취소</button>
              <button
                className="text-xs px-3 py-1.5 rounded-full bg-red-600 text-white hover:bg-red-500 disabled:opacity-50"
                disabled={deleting}
                onClick={() => void runDelete(confirmIds)}
              >
                {deleting ? '삭제 중...' : '삭제'}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 card !py-2 !px-4 text-sm shadow-2xl">{toast}</div>
      )}
    </div>
  )
}
