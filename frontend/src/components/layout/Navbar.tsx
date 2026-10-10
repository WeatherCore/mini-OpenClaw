import { SidebarSimple, Scales } from '@phosphor-icons/react'
import { useStore } from '@/store'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'

/** 顶栏：左栏开关 + 品牌 + Token/RAG 状态 + 右栏开关 */
export function Navbar() {
  const toggleSidebar = useStore(s => s.toggleSidebar)
  const toggleInspector = useStore(s => s.toggleInspector)
  const sidebarOpen = useStore(s => s.sidebarOpen)
  const inspectorOpen = useStore(s => s.inspectorOpen)
  const sessionTokens = useStore(s => s.sessionTokens)
  const ragMode = useStore(s => s.ragMode)

  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b border-border-default bg-canvas-default px-2">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant={sidebarOpen ? 'subtle' : 'ghost'}
            size="icon-sm"
            onClick={toggleSidebar}
            aria-label="切换会话列表"
          >
            <SidebarSimple size={16} />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">会话列表（{sidebarOpen ? '展开' : '收起'}）</TooltipContent>
      </Tooltip>

      <div className="flex items-center gap-1.5 px-1">
        <span className="text-base">🦞</span>
        <span className="text-sm font-semibold text-fg-default">mini OpenClaw</span>
      </div>

      <div className="ml-auto flex items-center gap-2 text-xs text-fg-muted">
        {sessionTokens && (
          <span className="flex items-center gap-1 rounded bg-canvas-subtle px-2 py-0.5 font-mono">
            <Scales size={12} />
            {sessionTokens.total_tokens}
          </span>
        )}
        {ragMode && (
          <span className="rounded bg-done-subtle px-1.5 py-0.5 font-medium text-done">
            RAG
          </span>
        )}
      </div>

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant={inspectorOpen ? 'subtle' : 'ghost'}
            size="icon-sm"
            onClick={toggleInspector}
            aria-label="切换检查器"
          >
            <SidebarSimple size={16} className="rotate-180" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">检查器（{inspectorOpen ? '展开' : '收起'}）</TooltipContent>
      </Tooltip>
    </header>
  )
}
