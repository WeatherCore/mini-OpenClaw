import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { WarningCircle, User, Robot } from '@phosphor-icons/react'
import { cn } from '@/lib/utils'
import type { ChatBubble } from '@/types'
import { ThoughtChain } from './ThoughtChain'
import { RetrievalCard } from './RetrievalCard'

/**
 * 单条消息气泡。
 * user：右对齐 + accent 实底 + 纯文本（不渲染 Markdown）
 * assistant：左对齐 + 灰底 + Markdown 渲染 + 思考链 + 检索卡
 * 空 + streaming：纯打字光标气泡
 */
export function ChatMessage({ bubble }: { bubble: ChatBubble }) {
  const isUser = bubble.role === 'user'
  const empty =
    !bubble.content && !bubble.toolCalls?.length && !bubble.retrieval

  return (
    <div className={cn('flex gap-2.5 px-4 py-3', isUser ? 'flex-row-reverse' : 'flex-row')}>
      <div
        className={cn(
          'grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs',
          isUser
            ? 'bg-accent text-white'
            : 'border border-border-default bg-canvas-subtle text-fg-muted',
        )}
      >
        {isUser ? <User size={14} weight="bold" /> : <Robot size={14} weight="bold" />}
      </div>
      <div
        className={cn(
          'flex min-w-0 max-w-[85%] flex-col',
          isUser ? 'items-end' : 'items-start',
        )}
      >
        {bubble.retrieval && (
          <RetrievalCard
            query={bubble.retrieval.query}
            results={bubble.retrieval.results}
          />
        )}
        {bubble.toolCalls && <ThoughtChain calls={bubble.toolCalls} />}
        {bubble.content && (
          <div
            className={cn(
              'break-words rounded-2xl px-3.5 py-2 text-sm leading-6',
              isUser
                ? 'rounded-tr-sm bg-accent text-white'
                : 'rounded-tl-sm border border-border-muted bg-canvas-subtle text-fg-default',
            )}
          >
            {isUser ? (
              <div className="whitespace-pre-wrap">{bubble.content}</div>
            ) : (
              <div className="md-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {bubble.content}
                </ReactMarkdown>
              </div>
            )}
            {bubble.streaming && bubble.content && <span className="caret" />}
          </div>
        )}
        {empty && bubble.streaming && (
          <div className="rounded-2xl border border-border-muted bg-canvas-subtle px-3.5 py-2.5 text-fg-muted">
            <span className="caret" />
          </div>
        )}
        {bubble.error && (
          <div className="mt-1 flex items-center gap-1.5 text-xs text-danger">
            <WarningCircle size={13} weight="bold" />
            <span>{bubble.error}</span>
          </div>
        )}
      </div>
    </div>
  )
}
