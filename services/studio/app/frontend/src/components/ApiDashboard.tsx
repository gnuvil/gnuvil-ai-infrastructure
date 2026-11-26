import { useCallback, useEffect, useMemo, useState } from 'react'

type RequestEvent = {
  id: number
  created_at: string
  endpoint: string
  payload: Record<string, unknown>
  generation_id?: number | null
  result?: {
    id: number
    status: string
    image_path: string | null
    media_type: string
    created_at?: string
    started_at?: string | null
    finished_at?: string | null
    wait_seconds?: number | null
    run_seconds?: number | null
    total_seconds?: number | null
  } | null
}

function getPrompt(payload: Record<string, unknown>): string {
  const direct = payload.prompt
  if (typeof direct === 'string' && direct.trim()) return direct
  const nested = payload.settings
  if (nested && typeof nested === 'object' && 'prompt' in nested) {
    const prompt = (nested as Record<string, unknown>).prompt
    if (typeof prompt === 'string') return prompt
  }
  return '프롬프트 없음'
}

function formatDuration(sec: number | null | undefined): string {
  if (sec == null || Number.isNaN(sec)) return '-'
  if (sec < 60) return `${sec < 10 ? sec.toFixed(1) : Math.round(sec)}초`
  const m = Math.floor(sec / 60)
  const r = Math.round(sec % 60)
  return r ? `${m}분 ${r}초` : `${m}분`
}

/** Seconds since an ISO time, for jobs still waiting or running. */
function since(iso: string | null | undefined): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  return Number.isNaN(t) ? null : Math.max(0, (Date.now() - t) / 1000)
}

const STATUS_LABEL: Record<string, string> = { queued: '대기 중', running: '생성 중', completed: '완료', failed: '실패', cancelled: '취소' }

/** Status badge text and colour for a request's generation, with the time it took (or is taking). */
function timingBadge(result: RequestEvent['result']): { text: string; tone: string } | null {
  if (!result) return null
  const label = STATUS_LABEL[result.status] ?? result.status
  if (result.status === 'running') return { text: `${label} · ${formatDuration(since(result.started_at))}`, tone: 'border-violet-500/40 bg-violet-500/10 text-violet-200' }
  if (result.status === 'queued') return { text: `${label} · ${formatDuration(since(result.created_at))}`, tone: 'border-neutral-700 bg-neutral-900 text-neutral-400' }
  const tone =
    result.status === 'completed'
      ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
      : result.status === 'failed'
        ? 'border-red-500/30 bg-red-500/10 text-red-300'
        : 'border-neutral-700 bg-neutral-900 text-neutral-400'
  return { text: result.run_seconds != null ? `${label} · ${formatDuration(result.run_seconds)}` : label, tone }
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

export default function ApiDashboard({ onBack }: { onBack: () => void }) {
  const [requests, setRequests] = useState<RequestEvent[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [endpointFilter, setEndpointFilter] = useState('all')
  const [showImage, setShowImage] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const response = await fetch('/api/request-logs?limit=500', { cache: 'no-store' })
      if (!response.ok) throw new Error(`요청 목록을 불러오지 못했습니다 (${response.status})`)
      const data = (await response.json()) as { requests: RequestEvent[] }
      setRequests(data.requests)
      setError('')
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 2000)
    return () => window.clearInterval(timer)
  }, [refresh])

  const endpoints = useMemo(() => [...new Set(requests.map((item) => item.endpoint))].sort(), [requests])
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return requests.filter((item) => {
      if (endpointFilter !== 'all' && item.endpoint !== endpointFilter) return false
      return !needle || `${item.endpoint} ${getPrompt(item.payload)} ${JSON.stringify(item.payload)}`.toLowerCase().includes(needle)
    })
  }, [requests, query, endpointFilter])
  const selected = filtered.find((item) => item.id === selectedId) ?? filtered[0]
  const avgRun = useMemo(() => {
    const done = filtered.map((i) => i.result).filter((r) => r?.status === 'completed' && r.run_seconds != null)
    return done.length ? { sec: done.reduce((s, r) => s + (r!.run_seconds ?? 0), 0) / done.length, n: done.length } : null
  }, [filtered])

  async function clearLogs() {
    if (!window.confirm('현재 메모리에 보관된 요청 로그를 모두 비울까요?')) return
    const response = await fetch('/api/request-logs', { method: 'DELETE' })
    if (!response.ok) {
      setError(`로그를 비우지 못했습니다 (${response.status})`)
      return
    }
    setRequests([])
    setSelectedId(null)
  }

  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-neutral-800 px-6 py-4">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-violet-400">Local Image AI</p>
          <h1 className="mt-1 text-xl font-semibold">API 요청 대시보드</h1>
        </div>
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-2 rounded-full border border-neutral-800 px-3 py-1.5 text-xs text-neutral-400">
            <span className={`h-2 w-2 rounded-full ${error ? 'bg-red-400' : 'bg-emerald-400'}`} />
            {error ? '연결 오류' : '2초마다 갱신'}
          </span>
          <button onClick={() => void refresh()} className="rounded-lg border border-neutral-700 px-3 py-2 text-sm hover:bg-neutral-900">새로고침</button>
          <button onClick={onBack} className="rounded-lg bg-violet-600 px-3 py-2 text-sm font-medium hover:bg-violet-500">생성 화면</button>
        </div>
      </header>

      <div className="mx-auto max-w-7xl space-y-5 p-6">
        {error && <div className="rounded-xl border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-300">{error}</div>}
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-4">
            <p className="text-sm text-neutral-400">보관된 요청</p><p className="mt-2 text-3xl font-semibold">{requests.length}<span className="ml-1 text-sm text-neutral-500">/ 500</span></p>
          </div>
          <div className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-4">
            <p className="text-sm text-neutral-400">필터 결과</p><p className="mt-2 text-3xl font-semibold">{filtered.length}</p>
          </div>
          <div className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-4">
            <p className="text-sm text-neutral-400">평균 생성 시간</p>
            <p className="mt-2 text-3xl font-semibold">{avgRun ? formatDuration(avgRun.sec) : '-'}{avgRun && <span className="ml-1 text-sm text-neutral-500">완료 {avgRun.n}건</span>}</p>
          </div>
          <div className="flex items-center justify-between rounded-2xl border border-neutral-800 bg-neutral-900/60 p-4">
            <div><p className="text-sm text-neutral-400">API 종류</p><p className="mt-2 text-3xl font-semibold">{endpoints.length}</p></div>
            <button onClick={() => void clearLogs()} disabled={!requests.length} className="rounded-lg border border-neutral-700 px-3 py-2 text-sm text-neutral-300 hover:border-red-800 hover:text-red-300 disabled:opacity-40">로그 비우기</button>
          </div>
        </section>

        <section className="grid min-h-[560px] gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
          <div className="overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900/40">
            <div className="flex flex-wrap gap-2 border-b border-neutral-800 p-3">
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="프롬프트나 설정 검색" className="min-w-48 flex-1 rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm outline-none focus:border-violet-500" />
              <select value={endpointFilter} onChange={(e) => setEndpointFilter(e.target.value)} className="rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm">
                <option value="all">모든 API</option>{endpoints.map((endpoint) => <option key={endpoint} value={endpoint}>{endpoint}</option>)}
              </select>
            </div>
            <div className="max-h-[620px] overflow-auto">
              {filtered.length === 0 ? <p className="p-8 text-center text-sm text-neutral-500">{requests.length ? '조건에 맞는 요청이 없습니다.' : '아직 들어온 요청이 없습니다.'}</p> : filtered.map((item) => (
                <button key={item.id} onClick={() => setSelectedId(item.id)} className={`block w-full border-b border-neutral-800/80 p-4 text-left hover:bg-neutral-900 ${selected?.id === item.id ? 'bg-violet-500/10' : ''}`}>
                  <div className="flex items-center justify-between gap-3"><span className="font-mono text-sm text-violet-300">{item.endpoint}</span><span className="shrink-0 text-xs text-neutral-500">{formatTime(item.created_at)}</span></div>
                  <p className="mt-2 line-clamp-2 text-sm text-neutral-300">{getPrompt(item.payload)}</p>
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <p className="text-xs text-neutral-500">요청 #{item.id}</p>
                    {(() => {
                      const b = timingBadge(item.result)
                      return b ? <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] tabular-nums ${b.tone}`}>{b.text}</span> : null
                    })()}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <aside className="rounded-2xl border border-neutral-800 bg-neutral-900/40 p-4">
            <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">요청 상세</h2>{selected && <span className="text-xs text-neutral-500">#{selected.id}</span>}</div>
            {selected ? <>
              <p className="mb-1 text-xs text-neutral-500">{selected.endpoint} · {formatTime(selected.created_at)}</p>
              <div className="mb-4 rounded-xl border border-neutral-800 bg-neutral-950 p-3"><p className="mb-2 text-xs font-medium text-neutral-500">프롬프트</p><p className="whitespace-pre-wrap break-words text-sm leading-6">{getPrompt(selected.payload)}</p></div>
              <section className="mb-4 overflow-hidden rounded-xl border border-neutral-800 bg-neutral-950">
                <div className="flex items-center justify-between border-b border-neutral-800 px-3 py-2">
                  <div><p className="text-xs font-medium text-neutral-300">생성 결과</p><p className="mt-0.5 text-[11px] text-neutral-500">{selected.result ? `작업 #${selected.result.id} · ${selected.result.status}` : selected.generation_id ? `작업 #${selected.generation_id} 연결 중` : '생성 작업에 연결되지 않은 요청'}</p></div>
                  <button disabled={!selected.result?.image_path} onClick={() => setShowImage((value) => !value)} className="rounded-md border border-neutral-700 px-2.5 py-1 text-xs text-neutral-300 hover:bg-neutral-900 disabled:opacity-40">{showImage ? '이미지 숨기기' : '이미지 보기'}</button>
                </div>
                {selected.result && (
                  <div className="grid grid-cols-3 gap-2 border-b border-neutral-800 px-3 py-3 text-center">
                    {([
                      ['대기', selected.result.wait_seconds ?? (selected.result.status === 'queued' ? since(selected.result.created_at) : null)],
                      ['생성', selected.result.run_seconds ?? (selected.result.status === 'running' ? since(selected.result.started_at) : null)],
                      ['전체', selected.result.total_seconds ?? (['queued', 'running'].includes(selected.result.status) ? since(selected.result.created_at) : null)],
                    ] as [string, number | null][]).map(([label, sec]) => (
                      <div key={label}>
                        <p className="text-[11px] text-neutral-500">{label}</p>
                        <p className="mt-0.5 text-sm font-medium tabular-nums text-neutral-200">{formatDuration(sec)}</p>
                      </div>
                    ))}
                  </div>
                )}
                {showImage && selected.result?.image_path && (
                  <div className="p-3">
                    {selected.result.media_type === 'video' || /\.(mp4|webm|mkv)(\?|$)/i.test(selected.result.image_path)
                      ? <video key={selected.result.image_path} src={selected.result.image_path} controls className="max-h-[360px] w-full rounded-lg bg-black" />
                      : <img key={selected.result.image_path} src={selected.result.image_path} alt={`요청 #${selected.id} 결과`} className="max-h-[420px] w-full rounded-lg bg-black object-contain" />}
                  </div>
                )}
                {showImage && selected.result && !selected.result.image_path && <p className="px-3 py-5 text-center text-xs text-neutral-500">{selected.result.status === 'failed' ? '생성에 실패했습니다.' : selected.result.status === 'cancelled' ? '생성이 취소되었습니다.' : '이미지 생성 중… 완료되면 여기에 표시됩니다.'}</p>}
              </section>
              <p className="mb-2 text-xs font-medium text-neutral-500">전체 설정</p>
              <pre className="max-h-[390px] overflow-auto rounded-xl border border-neutral-800 bg-neutral-950 p-3 text-xs leading-5 text-emerald-200">{JSON.stringify(selected.payload, null, 2)}</pre>
            </> : <p className="py-12 text-center text-sm text-neutral-500">왼쪽 목록에서 요청을 선택하세요.</p>}
          </aside>
        </section>
        <p className="text-xs text-neutral-600">요청 내용은 서버 메모리에 최근 500건까지 보관되며, 서버를 재시작하면 초기화됩니다.</p>
      </div>
    </main>
  )
}
