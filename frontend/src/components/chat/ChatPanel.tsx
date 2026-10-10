import * as React from 'react'
import { Sparkle } from '@phosphor-icons/react'
import { useStore } from '@/store'
import { ChatMessage } from './ChatMessage'
import { ChatInput } from './ChatInput'

const QUICK_PROMPTS = [
  '你是谁？',
  '查一下北京今天的天气',
  '帮我读 memory/MEMORY.md',
  '解释一下你的技能系统',
]

/**
 * 对话流面板：消息列表 + 自动滚底 + 空状态快捷提问 + 输入框。
 */
export function ChatPanel() {
  const bubbles = useStore(s => s.bubbles)
  const streaming = useStore(s => s.streaming)
  const sendMessage = useStore(s => s.sendMessage)
  const scrollRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [bubbles])

  if (bubbles.length === 0) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6">
          <div className="grid h-12 w-12 place-items-center rounded-xl bg-accent/10 text-accent">
            <Sparkle size={24} weight="bold" />
          </div>
          <div className="text-center">
            <div className="text-lg font-semibold text-fg-default">mini OpenClaw</div>
            <p className="text-sm text-fg-muted">
              文件即记忆 · 技能即插件 · 全程透明
            </p>
          </div>
          <div className="flex max-w-md flex-wrap justify-center gap-2">
            {QUICK_PROMPTS.map(q => (
              <button
                key={q}
                onClick={() => void sendMessage(q)}
                disabled={streaming}
                className="rounded-full border border-border-default bg-canvas-default px-3 py-1.5 text-xs text-fg-default transition-colors hover:border-accent hover:text-accent disabled:opacity-40"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
        <ChatInput />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {bubbles.map(b => (
          <ChatMessage key={b.id} bubble={b} />
        ))}
      </div>
      <ChatInput />
    </div>
  )
}
