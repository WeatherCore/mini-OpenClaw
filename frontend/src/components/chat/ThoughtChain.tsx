import {
  Terminal, Code, GlobeSimple, FileText, MagnifyingGlass,
  CircleNotch, CaretDown,
} from '@phosphor-icons/react'
import type { BubbleToolCall } from '@/types'
import { TOOL_META } from '@/types'

const ICONS = {
  terminal: Terminal,
  python: Code,
  fetch: GlobeSimple,
  readfile: FileText,
  search: MagnifyingGlass,
} as const

/**
 * 思考链：每个工具调用一张可折叠卡片
 * running → CircleNotch 转圈；done → 工具图标 + 打勾式样
 */
export function ThoughtChain({ calls }: { calls: BubbleToolCall[] }) {
  if (!calls.length) return null
  return (
    <div className="flex flex-col gap-1.5 my-2">
      {calls.map((c, i) => {
        const meta = TOOL_META[c.tool] ?? {
          label: c.tool,
          colorVar: '--color-neutral',
          icon: 'terminal' as const,
        }
        const Icon = ICONS[meta.icon] ?? Terminal
        const color = `var(${meta.colorVar})`
        return (
          <div
            key={i}
            className="rounded-md border border-border-muted bg-canvas-subtle text-xs"
          >
            <details className="group">
              <summary className="flex cursor-pointer select-none items-center gap-2 px-2.5 py-1.5 list-none">
                <span className="grid h-4 w-4 place-items-center" style={{ color }}>
                  {c.status === 'running' ? (
                    <CircleNotch size={14} className="spin" />
                  ) : (
                    <Icon size={14} weight="bold" />
                  )}
                </span>
                <span className="font-medium text-fg-default">{meta.label}</span>
                {c.status === 'running' && (
                  <span className="text-fg-muted">运行中…</span>
                )}
                <CaretDown
                  size={12}
                  className="ml-auto text-fg-subtle transition-transform group-open:rotate-180"
                />
              </summary>
              <div className="space-y-1.5 border-t border-border-muted px-2.5 py-2">
                <div>
                  <div className="mb-0.5 text-fg-subtle">输入</div>
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-canvas-inset p-1.5 font-mono text-[11px] text-fg-muted">
                    {c.input || '(空)'}
                  </pre>
                </div>
                {c.status === 'done' && (
                  <div>
                    <div className="mb-0.5 text-fg-subtle">输出</div>
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-canvas-inset p-1.5 font-mono text-[11px] text-fg-muted">
                      {c.output || '(空)'}
                    </pre>
                  </div>
                )}
              </div>
            </details>
          </div>
        )
      })}
    </div>
  )
}
