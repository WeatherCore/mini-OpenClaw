import * as React from 'react'
import { PaperPlaneTilt, Stop } from '@phosphor-icons/react'
import { useStore } from '@/store'

export function ChatInput() {
  const [text, setText] = React.useState('')
  const streaming = useStore(s => s.streaming)
  const sendMessage = useStore(s => s.sendMessage)
  const stop = useStore(s => s.stopStreaming)
  const taRef = React.useRef<HTMLTextAreaElement>(null)

  const autoGrow = () => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'
  }
  React.useLayoutEffect(autoGrow, [text])

  const submit = () => {
    const t = text.trim()
    if (!t || streaming) return
    setText('')
    void sendMessage(t)
  }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="border-t border-border-default bg-canvas-default px-4 py-3">
      <div className="flex items-end gap-2 rounded-lg border border-border-default bg-canvas-subtle px-3 py-2 focus-within:border-accent transition-colors">
        <textarea
          ref={taRef}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder={streaming ? '回答中…' : '给 mini OpenClaw 发消息（Enter 发送，Shift+Enter 换行）'}
          rows={1}
          className="flex-1 resize-none bg-transparent text-sm leading-6 text-fg-default placeholder:text-fg-subtle outline-none max-h-40"
        />
        {streaming ? (
          <button
            onClick={stop}
            className="grid h-7 w-7 place-items-center rounded text-fg-muted hover:bg-canvas-inset hover:text-danger"
            title="停止生成"
          >
            <Stop size={16} weight="fill" />
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!text.trim()}
            className="grid h-7 w-7 place-items-center rounded text-accent disabled:opacity-40 hover:bg-canvas-inset"
            title="发送"
          >
            <PaperPlaneTilt size={16} weight="fill" />
          </button>
        )}
      </div>
    </div>
  )
}
