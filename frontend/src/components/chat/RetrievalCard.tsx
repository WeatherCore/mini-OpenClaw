import { Brain, CaretDown } from '@phosphor-icons/react'
import type { RetrievalResult } from '@/types'

/** RAG 记忆检索结果卡片（done 紫色调，区别于绿 accent） */
export function RetrievalCard({
  query,
  results,
}: {
  query: string
  results: RetrievalResult[]
}) {
  return (
    <div className="my-2 rounded-md border border-done-subtle bg-done-subtle/40 text-xs">
      <details>
        <summary className="flex cursor-pointer select-none items-center gap-2 px-2.5 py-1.5 list-none">
          <Brain size={14} className="text-done" weight="bold" />
          <span className="font-medium text-fg-default">记忆检索</span>
          <span className="text-fg-muted">命中 {results.length} 条</span>
          <CaretDown size={12} className="ml-auto text-fg-subtle" />
        </summary>
        <div className="space-y-2 border-t border-border-muted px-2.5 py-2">
          <div className="text-fg-subtle">
            查询：<span className="text-fg-default">{query}</span>
          </div>
          {results.map((r, i) => (
            <div key={i} className="border-l-2 border-done/40 pl-2">
              <div className="mb-0.5 font-mono text-[10px] text-fg-subtle">
                相似度 {(r.score * 100).toFixed(0)}%
              </div>
              <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-fg-muted">
                {r.text}
              </pre>
            </div>
          ))}
        </div>
      </details>
    </div>
  )
}
