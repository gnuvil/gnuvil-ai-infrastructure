import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, KeyboardEvent } from 'react'
import { searchTags } from '../services/api'
import type { TagSuggestion } from '../services/api'

interface Props {
  value: string
  onChange: (value: string) => void
  multiline?: boolean
  rows?: number
  className?: string
  placeholder?: string
  dropUp?: boolean
  onEnter?: () => void
  disabled?: boolean
}

const HANGUL = /[ㄱ-ㆎ가-힣]/
const TOKEN_OK = /^[A-Za-z0-9_'()\-. ]{2,}$/

/** The text being typed after the last comma, e.g. "1girl, long h" -> "long h". */
function currentToken(text: string, caret: number): { token: string; start: number } {
  const before = text.slice(0, caret)
  const start = before.lastIndexOf(',') + 1
  const raw = before.slice(start)
  const lead = raw.length - raw.trimStart().length
  return { token: raw.trim(), start: start + lead }
}

/** Text input/textarea with Danbooru tag suggestions for the English token being typed. */
export default function TagSuggestField({
  value,
  onChange,
  multiline,
  rows,
  className,
  placeholder,
  dropUp,
  onEnter,
  disabled,
}: Props) {
  const ref = useRef<HTMLInputElement & HTMLTextAreaElement>(null)
  const [items, setItems] = useState<TagSuggestion[]>([])
  const [active, setActive] = useState(0)
  const [navigated, setNavigated] = useState(false)
  const [range, setRange] = useState<{ start: number; end: number } | null>(null)
  const seq = useRef(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  function refresh(text: string, caret: number) {
    const { token } = currentToken(text, caret)
    if (!TOKEN_OK.test(token) || HANGUL.test(token)) {
      setItems([])
      return
    }
    const id = ++seq.current
    if (timer.current) clearTimeout(timer.current)
    // longest phrase first, then progressively the last words only ("1girl long h" -> "long h" -> "h")
    const words = token.split(/\s+/)
    const candidates = words.map((_, i) => words.slice(i).join(' ')).filter((c) => c.length >= 2)
    timer.current = setTimeout(async () => {
      for (const c of candidates) {
        try {
          const res = await searchTags(c)
          if (id !== seq.current) return
          if (res.length > 0) {
            setItems(res)
            setActive(0)
            setNavigated(false)
            setRange({ start: caret - c.length, end: caret })
            return
          }
        } catch {
          break
        }
      }
      if (id === seq.current) setItems([])
    }, 150)
  }

  useEffect(
    () => () => {
      seq.current++
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )

  function accept(item: TagSuggestion) {
    if (!range) return
    const insert = `${item.tag}, `
    const next = value.slice(0, range.start) + insert + value.slice(range.end).replace(/^\s*,?\s*/, '')
    onChange(next)
    setItems([])
    const caret = range.start + insert.length
    requestAnimationFrame(() => {
      ref.current?.focus()
      ref.current?.setSelectionRange(caret, caret)
    })
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) {
    if (items.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setActive((a) => (a + 1) % items.length)
        setNavigated(true)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setActive((a) => (a - 1 + items.length) % items.length)
        setNavigated(true)
        return
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && navigated)) {
        e.preventDefault()
        accept(items[active])
        return
      }
      if (e.key === 'Escape') {
        setItems([])
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey && onEnter) {
      e.preventDefault()
      onEnter()
    }
  }

  const common = {
    ref,
    value,
    className,
    placeholder,
    disabled,
    onChange: (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      onChange(e.target.value)
      refresh(e.target.value, e.target.selectionStart ?? e.target.value.length)
    },
    onKeyDown,
    onBlur: () => setTimeout(() => setItems([]), 150),
  }

  return (
    <div className="relative flex-1 min-w-0">
      {multiline ? <textarea {...common} rows={rows} /> : <input {...common} />}
      {items.length > 0 && (
        <ul
          className={`absolute z-40 left-0 right-0 max-h-64 overflow-auto bg-neutral-900 border border-neutral-700 rounded-lg shadow-lg text-xs ${
            dropUp ? 'bottom-full mb-1' : 'top-full mt-1'
          }`}
        >
          {items.map((it, i) => (
            <li
              key={it.tag}
              className={`px-3 py-1.5 flex items-center gap-2 cursor-pointer ${i === active ? 'bg-violet-600/30' : 'hover:bg-neutral-800'}`}
              onMouseDown={(e) => {
                e.preventDefault()
                accept(it)
              }}
            >
              <span className="text-neutral-100 truncate">{it.tag}</span>
              {it.alias && <span className="text-neutral-500 truncate">← {it.alias}</span>}
              <span className="ml-auto text-neutral-500 shrink-0">
                {it.category} · {it.count.toLocaleString()}
              </span>
            </li>
          ))}
          <li className="px-3 py-1 text-[10px] text-neutral-600 border-t border-neutral-800">Tab 선택 · ↑↓ 이동 · Esc 닫기</li>
        </ul>
      )}
    </div>
  )
}
