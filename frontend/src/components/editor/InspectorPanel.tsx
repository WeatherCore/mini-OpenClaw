import * as React from 'react'
import {
  FileText, FloppyDisk, ArrowsOutSimple, Code, Brain,
} from '@phosphor-icons/react'
import { useStore } from '@/store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { ResizeHandle } from '@/components/layout/ResizeHandle'

// Monaco 体积大，懒加载
const MonacoEditor = React.lazy(() =>
  import('@monaco-editor/react').then(m => ({ default: m.default })),
)

const WORKSPACE_FILES = [
  'workspace/SOUL.md',
  'workspace/IDENTITY.md',
  'workspace/USER.md',
  'workspace/AGENTS.md',
  'memory/MEMORY.md',
  'SKILLS_SNAPSHOT.md',
]

const SIDE_MIN = 280
const SIDE_MAX = 640

/** 右侧检查器：Memory / Skills 双 Tab + Monaco 编辑器。可折叠、可拖拽、可全屏。 */
export function InspectorPanel() {
  const inspectorOpen = useStore(s => s.inspectorOpen)
  const [width, setWidth] = React.useState(360)
  const clamp = (w: number) => Math.min(SIDE_MAX, Math.max(SIDE_MIN, w))
  const [full, setFull] = React.useState(false)

  // Ctrl+S 全局保存
  const saveCurrentFile = useStore(s => s.saveCurrentFile)
  const currentPath = useStore(s => s.editor.currentPath)
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        if (currentPath) {
          e.preventDefault()
          void saveCurrentFile()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [currentPath, saveCurrentFile])

  if (!inspectorOpen) return null
  return (
    <>
      <ResizeHandle side="right" onResize={d => setWidth(w => clamp(w + d))} />
      <aside
        style={full ? { width: '100vw' } : { width }}
        className={
          full
            ? 'fixed inset-0 z-40 flex flex-col border-l border-border-default bg-canvas-default'
            : 'flex h-full shrink-0 flex-col border-l border-border-default bg-canvas-default'
        }
      >
        <Tabs defaultValue="memory" className="flex h-full flex-col">
          <div className="flex items-center justify-between pr-2">
            <TabsList>
              <TabsTrigger value="memory">
                <FileText size={13} className="mr-1" /> Memory
              </TabsTrigger>
              <TabsTrigger value="skills">
                <Brain size={13} className="mr-1" /> Skills
              </TabsTrigger>
            </TabsList>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setFull(f => !f)}
                  aria-label="全屏"
                >
                  <ArrowsOutSimple size={14} />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                {full ? '退出全屏' : '全屏编辑'}
              </TooltipContent>
            </Tooltip>
          </div>

          <TabsContent value="memory" className="min-h-0 flex-1">
            <MemoryTab />
          </TabsContent>
          <TabsContent value="skills" className="min-h-0 flex-1">
            <SkillsTab />
          </TabsContent>
        </Tabs>
      </aside>
    </>
  )
}

function MemoryTab() {
  const editor = useStore(s => s.editor)
  const openFile = useStore(s => s.openFile)
  const setEditorContent = useStore(s => s.setEditorContent)
  const saveCurrentFile = useStore(s => s.saveCurrentFile)

  // 初始打开 MEMORY.md
  React.useEffect(() => {
    if (!editor.currentPath) {
      void openFile('memory/MEMORY.md')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const dirty = editor.content !== editor.savedContent
  const tokens = editor.currentPath ? editor.fileTokens[editor.currentPath] : undefined

  return (
    <div className="flex h-full">
      <div className="w-40 shrink-0 overflow-y-auto border-r border-border-muted p-1.5">
        {WORKSPACE_FILES.map(p => {
          const name = p.split('/').pop()!
          const active = p === editor.currentPath
          const tk = editor.fileTokens[p]
          return (
            <button
              key={p}
              onClick={() => void openFile(p)}
              className={cn(
                'flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs transition-colors',
                active
                  ? 'bg-accent-subtle text-accent'
                  : 'text-fg-default hover:bg-canvas-subtle',
              )}
            >
              <FileText size={12} className="shrink-0 opacity-70" />
              <span className="flex-1 truncate">{name}</span>
              {tk != null && (
                <span className="font-mono text-[10px] text-fg-subtle">{tk}</span>
              )}
            </button>
          )
        })}
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        {editor.currentPath ? (
          <>
            <div className="flex h-8 items-center gap-2 border-b border-border-muted px-2 text-xs">
              <span className="truncate text-fg-default">{editor.currentPath}</span>
              {tokens != null && (
                <span className="font-mono text-[10px] text-fg-subtle">{tokens} tok</span>
              )}
              <div className="ml-auto flex items-center gap-1.5">
                {dirty && (
                  <span className="h-1.5 w-1.5 rounded-full bg-attention" title="未保存" />
                )}
                <span
                  className={
                    editor.saving
                      ? 'text-fg-muted'
                      : dirty
                        ? 'text-attention'
                        : 'text-accent'
                  }
                >
                  {editor.saving ? '保存中…' : dirty ? '未保存' : '已保存'}
                </span>
                <Button
                  variant="subtle"
                  size="icon-sm"
                  onClick={() => void saveCurrentFile()}
                  disabled={!dirty || editor.saving}
                  aria-label="保存"
                >
                  <FloppyDisk size={13} />
                </Button>
              </div>
            </div>
            <div className="min-h-0 flex-1">
              <React.Suspense
                fallback={<div className="p-3 text-xs text-fg-muted">编辑器加载中…</div>}
              >
                <MonacoEditor
                  language="markdown"
                  theme="vs"
                  value={editor.content}
                  onChange={(v: string | undefined) => setEditorContent(v ?? '')}
                  options={{
                    minimap: { enabled: false },
                    fontSize: 13,
                    wordWrap: 'on',
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    automaticLayout: true,
                    padding: { top: 8 },
                  }}
                />
              </React.Suspense>
            </div>
          </>
        ) : (
          <div className="grid flex-1 place-items-center text-xs text-fg-subtle">
            <Code size={20} className="mb-1 opacity-50" />
            选择文件查看
          </div>
        )}
      </div>
    </div>
  )
}

function SkillsTab() {
  const skills = useStore(s => s.skills)
  const openFile = useStore(s => s.openFile)
  const editor = useStore(s => s.editor)
  const setEditorContent = useStore(s => s.setEditorContent)

  return (
    <div className="flex h-full">
      <div className="w-44 shrink-0 overflow-y-auto border-r border-border-muted p-1.5">
        {skills.length === 0 ? (
          <div className="px-2 py-3 text-center text-[11px] text-fg-subtle">
            暂无技能
          </div>
        ) : (
          skills.map(sk => {
            const active = sk.path === editor.currentPath
            return (
              <button
                key={sk.path}
                onClick={() => void openFile(sk.path)}
                className={cn(
                  'mb-0.5 flex w-full flex-col gap-0.5 rounded px-2 py-1.5 text-left transition-colors',
                  active ? 'bg-accent-subtle' : 'hover:bg-canvas-subtle',
                )}
              >
                <span
                  className={cn(
                    'text-xs font-medium',
                    active ? 'text-accent' : 'text-fg-default',
                  )}
                >
                  {sk.name}
                </span>
                {sk.description && (
                  <span className="line-clamp-2 text-[10px] leading-tight text-fg-subtle">
                    {sk.description}
                  </span>
                )}
              </button>
            )
          })
        )}
      </div>

      <div className="min-w-0 flex-1">
        {editor.currentPath?.startsWith('skills/') ? (
          <React.Suspense
            fallback={<div className="p-3 text-xs text-fg-muted">加载中…</div>}
          >
            <MonacoEditor
              language="markdown"
              theme="vs"
              value={editor.content}
              onChange={(v: string | undefined) => setEditorContent(v ?? '')}
              options={{
                minimap: { enabled: false },
                fontSize: 13,
                wordWrap: 'on',
                readOnly: false,
                automaticLayout: true,
                padding: { top: 8 },
              }}
            />
          </React.Suspense>
        ) : (
          <div className="grid h-full place-items-center text-xs text-fg-subtle">
            点击技能读 SKILL.md
          </div>
        )}
      </div>
    </div>
  )
}
