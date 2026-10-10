import * as React from 'react'
import { toast, Toaster } from 'sonner'
import { useStore } from './store'
import { Navbar } from './components/layout/Navbar'
import { Sidebar } from './components/layout/Sidebar'
import { ChatPanel } from './components/chat/ChatPanel'
import { InspectorPanel } from './components/editor/InspectorPanel'
import { TooltipProvider } from './components/ui/tooltip'

export default function App() {
  const init = useStore(s => s.init)
  const error = useStore(s => s.error)
  const clearError = useStore(s => s.clearError)

  // 初始化：加载会话列表 / RAG 模式 / 技能，并选中首个会话
  React.useEffect(() => {
    void init()
  }, [init])

  // 错误短暂 toast
  React.useEffect(() => {
    if (error) {
      toast.error(error)
      clearError()
    }
  }, [error, clearError])

  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex h-screen flex-col bg-canvas-default">
        <Navbar />
        <div className="flex min-h-0 flex-1">
          <Sidebar />
          <main className="flex min-w-0 flex-1 flex-col">
            <ChatPanel />
          </main>
          <InspectorPanel />
        </div>
      </div>
      <Toaster
        position="bottom-center"
        toastOptions={{ style: { fontSize: '13px' } }}
      />
    </TooltipProvider>
  )
}
