import * as React from 'react'
import {
  Plus, ChatCircle, PencilSimple, Trash, FileCode,
  Database, ArrowsClockwise, Info,
} from '@phosphor-icons/react'
import { useStore } from '@/store'
import { cn, fmtRelative } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ResizeHandle } from './ResizeHandle'

const SIDE_MIN = 200
const SIDE_MAX = 420

/** 左侧栏：会话列表 + Raw Messages 折叠区。可折叠、可拖拽调宽。 */
export function Sidebar() {
  const sidebarOpen = useStore(s => s.sidebarOpen)
  const [width, setWidth] = React.useState(280)
  const clamp = (w: number) => Math.min(SIDE_MAX, Math.max(SIDE_MIN, w))

  if (!sidebarOpen) return null
  return (
    <>
      <aside
        style={{ width }}
        className="flex h-full shrink-0 flex-col border-r border-border-default bg-canvas-default"
      >
        <SessionList />
        <RawMessagesSection />
      </aside>
      <ResizeHandle side="left" onResize={d => setWidth(w => clamp(w + d))} />
    </>
  )
}

function SessionList() {
  const sessions = useStore(s => s.sessions)
  const currentId = useStore(s => s.currentSessionId)
  const streaming = useStore(s => s.streaming)
  const selectSession = useStore(s => s.selectSession)
  const newSession = useStore(s => s.newSession)
  const renameSession = useStore(s => s.renameSession)
  const removeSession = useStore(s => s.removeSession)

  const [editingId, setEditingId] = React.useState<string | null>(null)
  const [editText, setEditText] = React.useState('')

  const startEdit = (id: string, title: string) => {
    setEditingId(id)
    setEditText(title)
  }
  const commitEdit = () => {
    if (editingId && editText.trim()) {
      void renameSession(editingId, editText.trim())
    }
    setEditingId(null)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col border-b border-border-default">
      <div className="flex h-9 shrink-0 items-center justify-between px-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
          会话
        </span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => void newSession()}
              disabled={streaming}
              aria-label="新建会话"
            >
              <Plus size={14} />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">新建会话</TooltipContent>
        </Tooltip>
      </div>

      <div className="flex-1 overflow-y-auto px-1.5 pb-2">
        {sessions.length === 0 ? (
          <div className="px-2 py-4 text-center text-xs text-fg-subtle">
            暂无会话
          </div>
        ) : (
          sessions.map(s => {
            const active = s.id === currentId
            return (
              <div
                key={s.id}
                onClick={() => !streaming && void selectSession(s.id)}
                className={cn(
                  'group mb-0.5 flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1.5 text-sm transition-colors',
                  active
                    ? 'bg-accent-subtle text-accent'
                    : 'text-fg-default hover:bg-canvas-subtle',
                )}
              >
                <ChatCircle size={14} className="shrink-0 opacity-70" />
                {editingId === s.id ? (
                  <input
                    autoFocus
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    onClick={e => e.stopPropagation()}
                    onBlur={commitEdit}
                    onKeyDown={e => {
                      if (e.key === 'Enter') commitEdit()
                      if (e.key === 'Escape') setEditingId(null)
                    }}
                    className="min-w-0 flex-1 border-b border-accent bg-transparent text-sm outline-none"
                  />
                ) : (
                  <span className="flex-1 truncate">{s.title || 'New Chat'}</span>
                )}
                {editingId !== s.id && (
                  <span className="text-[10px] text-fg-subtle opacity-0 group-hover:opacity-100">
                    {fmtRelative(s.updated_at)}
                  </span>
                )}
                <div className="flex opacity-0 group-hover:opacity-100">
                  <button
                    onClick={e => {
                      e.stopPropagation()
                      startEdit(s.id, s.title)
                    }}
                    className="grid h-5 w-5 place-items-center text-fg-muted hover:text-fg-default"
                    title="重命名"
                  >
                    <PencilSimple size={12} />
                  </button>
                  <button
                    onClick={e => {
                      e.stopPropagation()
                      if (confirm('删除该会话？归档会保留在 sessions/archive/')) {
                        void removeSession(s.id)
                      }
                    }}
                    className="grid h-5 w-5 place-items-center text-fg-muted hover:text-danger"
                    title="删除"
                  >
                    <Trash size={12} />
                  </button>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

function RawMessagesSection() {
  const currentId = useStore(s => s.currentSessionId)
  const rawOpen = useStore(s => s.rawOpen)
  const setRawOpen = useStore(s => s.setRawOpen)
  const loadRaw = useStore(s => s.loadRawMessages)
  const rawMessages = useStore(s => s.rawMessages)
  const rawLoading = useStore(s => s.rawLoading)
  const ragMode = useStore(s => s.ragMode)
  const toggleRag = useStore(s => s.toggleRag)
  const compressCurrent = useStore(s => s.compressCurrent)
  const streaming = useStore(s => s.streaming)
  const sessionTokens = useStore(s => s.sessionTokens)
  const [fullOpen, setFullOpen] = React.useState(false)

  const toggle = () => {
    const next = !rawOpen
    setRawOpen(next)
    if (next && currentId) void loadRaw(currentId)
  }

  const sysPrompt =
    rawMessages?.messages.find(m => m.role === 'system')?.content ?? ''

  return (
    <div className="shrink-0">
      <button
        onClick={toggle}
        className="flex h-9 w-full items-center gap-1.5 px-3 text-xs font-semibold uppercase tracking-wide text-fg-muted hover:text-fg-default"
      >
        <FileCode size={14} />
        Raw Messages
      </button>

      {rawOpen && (
        <div className="flex flex-col gap-2 px-3 pb-3 text-xs">
          {/* System Prompt 预览 */}
          <div>
            <div className="mb-1 text-fg-subtle">System Prompt（前 300 字）</div>
            <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words rounded border border-border-muted bg-canvas-subtle p-1.5 font-mono text-[10px] text-fg-muted">
              {rawLoading ? '加载中…' : sysPrompt.slice(0, 300) + (sysPrompt.length > 300 ? '…' : '') || '(无)'}
            </pre>
            <button
              onClick={() => setFullOpen(true)}
              className="mt-1 text-accent hover:underline"
            >
              查看完整 →
            </button>
          </div>

          {/* Token 统计 */}
          {sessionTokens && (
            <div className="flex items-center justify-between rounded border border-border-muted bg-canvas-subtle px-2 py-1.5">
              <span className="text-fg-muted">上下文 Token</span>
              <span className="font-mono text-fg-default">
                {sessionTokens.compressed_tokens
                  ? <>{sessionTokens.message_tokens}+{sessionTokens.compressed_tokens}+{sessionTokens.system_tokens}=</>
                  : <>{sessionTokens.message_tokens}+{sessionTokens.system_tokens}=</>
                }
                <b className="text-accent">{sessionTokens.total_tokens}</b>
              </span>
            </div>
          )}

          {/* RAG 开关 */}
          <div className="flex items-center justify-between rounded border border-border-muted bg-canvas-subtle px-2 py-1.5">
            <span className="flex items-center gap-1.5 text-fg-muted">
              <Database size={12} /> RAG 检索
            </span>
            <Switch checked={ragMode} onCheckedChange={() => void toggleRag()} />
          </div>

          {/* 压缩 */}
          <Button
            variant="subtle"
            size="sm"
            onClick={() => void compressCurrent()}
            disabled={streaming}
            className="justify-start"
          >
            <ArrowsClockwise size={13} /> 压缩历史
          </Button>
        </div>
      )}

      <Dialog open={fullOpen} onOpenChange={setFullOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Info size={16} className="text-accent" /> 完整 System Prompt
            </DialogTitle>
          </DialogHeader>
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs text-fg-muted">
            {rawLoading ? '加载中…' : sysPrompt || '(空)'}
          </pre>
        </DialogContent>
      </Dialog>
    </div>
  )
}
