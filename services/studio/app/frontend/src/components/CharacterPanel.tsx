import { useState } from 'react'
import type { ChangeEvent } from 'react'
import { createCharacter, deleteCharacter, updateCharacter } from '../services/api'
import type { Character, LoRAConfig } from '../types'
import TagSuggestField from './TagSuggestField'

interface Props {
  characters: Character[]
  activeIds: number[]
  onToggle: (id: number) => void
  onChanged: () => void
  availableLoras: string[]
}

interface Draft {
  id: number | null
  name: string
  aliases: string
  tags: string
  loras: LoRAConfig[]
  useReference: boolean
  ipWeight: number
  notes: string
  file: File | null
  referenceImage: string | null
  removeReference: boolean
}

const EMPTY: Draft = {
  id: null, name: '', aliases: '', tags: '', loras: [], useReference: false, ipWeight: 0.2, notes: '',
  file: null, referenceImage: null, removeReference: false,
}

const input = 'w-full bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1 text-xs'

export default function CharacterPanel({ characters, activeIds, onToggle, onChanged, availableLoras }: Props) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  function edit(c: Character) {
    setError('')
    setDraft({
      id: c.id, name: c.name, aliases: c.aliases, tags: c.tags, loras: c.loras, useReference: c.use_reference,
      ipWeight: c.ip_weight, notes: c.notes, file: null, referenceImage: c.reference_image, removeReference: false,
    })
  }

  async function save() {
    if (!draft) return
    if (!draft.name.trim()) {
      setError('이름을 입력하세요.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const form = new FormData()
      form.set('name', draft.name.trim())
      form.set('aliases', draft.aliases)
      form.set('tags', draft.tags)
      form.set('loras', JSON.stringify(draft.loras))
      form.set('use_reference', String(draft.useReference))
      form.set('ip_weight', String(draft.ipWeight))
      form.set('notes', draft.notes)
      if (draft.file) form.set('file', draft.file)
      if (draft.id !== null) {
        if (draft.removeReference && !draft.file) form.set('remove_reference', 'true')
        await updateCharacter(draft.id, form)
      } else {
        await createCharacter(form)
      }
      setDraft(null)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove(c: Character) {
    if (!window.confirm(`캐릭터 '${c.name}'을(를) 삭제할까요?`)) return
    try {
      await deleteCharacter(c.id)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  function setLoraWeight(name: string, weight: number) {
    setDraft((d) => (d ? { ...d, loras: d.loras.map((l) => (l.name === name ? { ...l, weight } : l)) } : d))
  }

  return (
    <div className="mt-2 space-y-2">
      {characters.length === 0 && !draft && (
        <p className="text-xs text-neutral-500">
          저장된 캐릭터가 없습니다. 생성한 이미지 아래의 "👤 캐릭터로 저장"이나 아래 버튼으로 만드세요.
        </p>
      )}

      {characters.map((c) => (
        <div key={c.id} className="flex items-center gap-2 bg-neutral-900 border border-neutral-800 rounded-md px-2 py-1.5">
          {c.reference_image ? (
            <img src={c.reference_image} alt="" className="w-8 h-8 rounded object-cover shrink-0" />
          ) : (
            <div className="w-8 h-8 rounded bg-neutral-800 shrink-0 flex items-center justify-center text-neutral-600 text-xs">👤</div>
          )}
          <div className="min-w-0 flex-1">
            <p className="text-xs text-neutral-200 truncate" title={c.tags}>
              {c.name}
            </p>
            <p className="text-[10px] text-neutral-500 truncate">
              {c.loras.length > 0 ? `LoRA ${c.loras.length}개` : 'LoRA 없음'}
              {c.use_reference ? ' · 참조 이미지 사용' : ''}
            </p>
          </div>
          <label className="flex items-center gap-1 text-[11px] text-neutral-400" title="이름을 쓰지 않아도 모든 요청에 적용">
            <input type="checkbox" checked={activeIds.includes(c.id)} onChange={() => onToggle(c.id)} />
            항상
          </label>
          <button className="text-xs text-neutral-500 hover:text-neutral-300" onClick={() => edit(c)} title="편집">
            ✎
          </button>
          <button className="text-xs text-neutral-500 hover:text-red-400" onClick={() => remove(c)} title="삭제">
            🗑
          </button>
        </div>
      ))}

      {!draft && (
        <button
          className="w-full text-xs bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1.5 text-neutral-300 hover:bg-neutral-800"
          onClick={() => {
            setError('')
            setDraft({ ...EMPTY })
          }}
        >
          + 새 캐릭터
        </button>
      )}

      {draft && (
        <div className="bg-neutral-950 border border-neutral-700 rounded-md p-2 space-y-2">
          <p className="text-xs text-neutral-300 font-semibold">{draft.id === null ? '새 캐릭터' : `편집: ${draft.name}`}</p>
          <label className="block text-[11px] text-neutral-400">
            이름 (요청에 이 이름을 쓰면 자동 적용)
            <input className={input} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </label>
          <label className="block text-[11px] text-neutral-400">
            별칭 (쉼표로 구분, 예: Hebi, 히비)
            <input className={input} value={draft.aliases} onChange={(e) => setDraft({ ...draft, aliases: e.target.value })} />
          </label>
          <div className="text-[11px] text-neutral-400">
            <span>기본 태그 (영어, 항상 프롬프트 앞에 붙음)</span>
            <TagSuggestField
              multiline
              rows={3}
              className={`${input} resize-none`}
              value={draft.tags}
              onChange={(v) => setDraft({ ...draft, tags: v })}
              placeholder="hebi, 1girl, silver hair, blue eyes"
            />
          </div>

          <div className="space-y-1">
            <span className="text-[11px] text-neutral-400">LoRA</span>
            {draft.loras.map((l) => (
              <div key={l.name} className="flex items-center gap-2">
                <span className="text-[11px] text-neutral-300 truncate flex-1" title={l.name}>
                  {l.name}
                </span>
                <input
                  type="range"
                  min={-2}
                  max={2}
                  step={0.05}
                  className="w-16"
                  value={l.weight}
                  onChange={(e) => setLoraWeight(l.name, Number(e.target.value))}
                />
                <span className="text-[11px] text-neutral-400 w-8 text-right">{l.weight.toFixed(2)}</span>
                <button
                  className="text-xs text-neutral-500 hover:text-neutral-300"
                  onClick={() => setDraft({ ...draft, loras: draft.loras.filter((x) => x.name !== l.name) })}
                >
                  ✕
                </button>
              </div>
            ))}
            <select
              className={input}
              value=""
              onChange={(e) => {
                const name = e.target.value
                if (name) setDraft({ ...draft, loras: [...draft.loras, { name, weight: 0.8 }] })
              }}
            >
              <option value="">+ LoRA 추가</option>
              {availableLoras
                .filter((n) => !draft.loras.some((l) => l.name === n))
                .map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
            </select>
          </div>

          <div className="space-y-1">
            <span className="text-[11px] text-neutral-400">참조 이미지</span>
            <div className="flex items-center gap-2">
              {(draft.file || (draft.referenceImage && !draft.removeReference)) && (
                <img
                  src={draft.file ? URL.createObjectURL(draft.file) : draft.referenceImage!}
                  alt="reference"
                  className="w-10 h-10 rounded object-cover"
                />
              )}
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="text-[11px] text-neutral-400 w-full"
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setDraft({ ...draft, file: e.target.files?.[0] ?? null, removeReference: false })
                }
              />
              {draft.referenceImage && !draft.file && !draft.removeReference && (
                <button className="text-xs text-neutral-500 hover:text-red-400" onClick={() => setDraft({ ...draft, removeReference: true })}>
                  제거
                </button>
              )}
            </div>
            <label className="flex items-center gap-2 text-[11px] text-neutral-400">
              <input type="checkbox" checked={draft.useReference} onChange={(e) => setDraft({ ...draft, useReference: e.target.checked })} />
              생성할 때 참조 이미지로 사용 (IP-Adapter)
            </label>
            {draft.useReference && (
              <label className="block text-[11px] text-neutral-400">
                가중치 {draft.ipWeight.toFixed(2)} (V-Pred 모델은 0.15~0.25 권장, 높이면 화풍이 깨짐)
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  className="w-full"
                  value={draft.ipWeight}
                  onChange={(e) => setDraft({ ...draft, ipWeight: Number(e.target.value) })}
                />
              </label>
            )}
          </div>

          <label className="block text-[11px] text-neutral-400">
            메모
            <input className={input} value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
          </label>

          {error && <p className="text-xs text-red-400">{error}</p>}
          <div className="flex gap-2">
            <button
              className="flex-1 text-xs bg-violet-600 hover:bg-violet-500 disabled:bg-neutral-800 rounded-md px-2 py-1.5"
              disabled={busy}
              onClick={save}
            >
              {busy ? '저장 중...' : '저장'}
            </button>
            <button className="text-xs bg-neutral-900 border border-neutral-700 rounded-md px-3 py-1.5 text-neutral-300" onClick={() => setDraft(null)}>
              취소
            </button>
          </div>
        </div>
      )}
      {!draft && error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  )
}
