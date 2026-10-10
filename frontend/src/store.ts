/* ============================================================
   mini OpenClaw · Zustand 状态中枢
   SSE 事件驱动气泡状态机 + 会话/文件/RAG/Token 全局状态
   替代原前端 524 行 Context 裸写（高频更新下 re-render 地狱）
   ============================================================ */
import { create } from 'zustand'
import * as api from './lib/api'
import type {
  SessionMeta, ChatBubble, BubbleToolCall, StoredMessage,
  MessagesResponse, SkillItem, SessionTokens, SSEEvent,
} from './types'

interface FileEditorState {
  currentPath: string | null
  content: string
  savedContent: string
  saving: boolean
  fileTokens: Record<string, number>
}

interface State {
  /* 会话 */
  sessions: SessionMeta[]
  currentSessionId: string | null
  bubbles: ChatBubble[]
  streaming: boolean
  abortCtrl: AbortController | null

  /* RAG */
  ragMode: boolean
  ragToggling: boolean

  /* 布局：可折叠三栏 */
  sidebarOpen: boolean
  inspectorOpen: boolean

  /* Raw Messages 面板 */
  rawMessages: MessagesResponse | null
  rawLoading: boolean
  rawOpen: boolean

  /* Token */
  sessionTokens: SessionTokens | null

  /* 技能 */
  skills: SkillItem[]

  /* 文件编辑器 */
  editor: FileEditorState

  /* 全局错误（短暂展示） */
  error: string | null

  /* ---- actions ---- */
  init: () => Promise<void>
  selectSession: (id: string) => Promise<void>
  newSession: () => Promise<void>
  renameSession: (id: string, title: string) => Promise<void>
  removeSession: (id: string) => Promise<void>
  sendMessage: (text: string) => Promise<void>
  stopStreaming: () => void
  toggleRag: () => Promise<void>
  compressCurrent: () => Promise<void>
  toggleSidebar: () => void
  toggleInspector: () => void
  loadRawMessages: (id?: string) => Promise<void>
  setRawOpen: (open: boolean) => void
  openFile: (path: string) => Promise<void>
  setEditorContent: (content: string) => void
  saveCurrentFile: () => Promise<void>
  refreshFileTokens: (paths: string[]) => Promise<void>
  clearError: () => void
}

let bubbleSeq = 0
const newBubbleId = () => `b-${Date.now()}-${++bubbleSeq}`

/** history 的 StoredMessage[] → 气泡数组（system 摘要注入消息跳过不显示） */
function historyToBubbles(msgs: StoredMessage[]): ChatBubble[] {
  const bubbles: ChatBubble[] = []
  for (const m of msgs) {
    if (m.role === 'system') continue
    if (m.role === 'user') {
      bubbles.push({ id: newBubbleId(), role: 'user', content: m.content })
    } else if (m.role === 'assistant') {
      bubbles.push({
        id: newBubbleId(),
        role: 'assistant',
        content: m.content,
        toolCalls: m.tool_calls?.map<BubbleToolCall>(tc => ({
          tool: tc.tool,
          input: tc.input,
          output: tc.output,
          status: 'done',
        })),
      })
    }
  }
  return bubbles
}

/** 把一个 SSE 事件应用到气泡状态（set 回调内调用，返回新 bubbles） */
function applyEvent(
  bubbles: ChatBubble[],
  currentId: string,
  ev: SSEEvent,
): { bubbles: ChatBubble[]; nextCurrentId: string } {
  let nextCurrentId = currentId
  const patch = (id: string, fn: (b: ChatBubble) => ChatBubble) =>
    bubbles.map(b => (b.id === id ? fn(b) : b))

  switch (ev.type) {
    case 'retrieval':
      return {
        bubbles: patch(currentId, b => ({ ...b, retrieval: { query: ev.query, results: ev.results } })),
        nextCurrentId,
      }

    case 'token':
      return {
        bubbles: patch(currentId, b => ({ ...b, content: b.content + ev.content })),
        nextCurrentId,
      }

    case 'new_response': {
      // 先把旧气泡标记非 streaming，再开新气泡
      const newBubble: ChatBubble = { id: newBubbleId(), role: 'assistant', content: '', streaming: true }
      nextCurrentId = newBubble.id
      return {
        bubbles: patch(currentId, b => ({ ...b, streaming: false })).concat(newBubble),
        nextCurrentId,
      }
    }

    case 'tool_start': {
      const tc: BubbleToolCall = { tool: ev.tool, input: ev.input, status: 'running' }
      return {
        bubbles: patch(currentId, b => ({ ...b, toolCalls: [...(b.toolCalls ?? []), tc] })),
        nextCurrentId,
      }
    }

    case 'tool_end': {
      // 逆序找第一个同名且 running 的 toolCall，填 output 并标 done
      return {
        bubbles: patch(currentId, b => {
          if (!b.toolCalls) return b
          const calls = [...b.toolCalls]
          for (let i = calls.length - 1; i >= 0; i--) {
            if (calls[i].tool === ev.tool && calls[i].status === 'running') {
              calls[i] = { ...calls[i], output: ev.output, status: 'done' }
              break
            }
          }
          return { ...b, toolCalls: calls }
        }),
        nextCurrentId,
      }
    }

    case 'done':
      return {
        bubbles: patch(currentId, b => ({
          ...b,
          streaming: false,
          // token 流已填了 content；done 的 content 作兜底（仅当气泡为空）
          content: b.content || ev.content,
        })),
        nextCurrentId,
      }

    case 'error':
      return {
        bubbles: patch(currentId, b => ({ ...b, streaming: false, error: ev.error })),
        nextCurrentId,
      }

    default:
      return { bubbles, nextCurrentId }
  }
}

export const useStore = create<State>((set, get) => ({
  sessions: [],
  currentSessionId: null,
  bubbles: [],
  streaming: false,
  abortCtrl: null,
  ragMode: false,
  ragToggling: false,
  sidebarOpen: true,
  inspectorOpen: true,
  rawMessages: null,
  rawLoading: false,
  rawOpen: false,
  sessionTokens: null,
  skills: [],
  editor: { currentPath: null, content: '', savedContent: '', saving: false, fileTokens: {} },
  error: null,

  init: async () => {
    try {
      const [sess, rag, skills] = await Promise.all([
        api.listSessions(),
        api.getRagMode(),
        api.listSkills(),
      ])
      set({ sessions: sess.sessions, ragMode: rag.rag_mode, skills: skills.skills })
      if (sess.sessions.length > 0) {
        await get().selectSession(sess.sessions[0].id)
      }
    } catch (e: any) {
      set({ error: e.message })
    }
  },

  selectSession: async (id) => {
    if (get().streaming) return
    set({ currentSessionId: id, bubbles: [], rawMessages: null, rawOpen: false, sessionTokens: null })
    try {
      const [hist, toks] = await Promise.all([
        api.getHistory(id),
        api.getSessionTokens(id),
      ])
      // 切换期间会话可能已被换走，二次校验
      if (get().currentSessionId !== id) return
      set({ bubbles: historyToBubbles(hist.messages), sessionTokens: toks })
    } catch (e: any) {
      set({ error: e.message })
    }
  },

  newSession: async () => {
    if (get().streaming) return
    try {
      const s = await api.createSession()
      set(st => ({
        sessions: [{ id: s.id, title: s.title, updated_at: s.updated_at }, ...st.sessions],
        currentSessionId: s.id,
        bubbles: [],
        rawMessages: null,
        rawOpen: false,
        sessionTokens: null,
      }))
    } catch (e: any) {
      set({ error: e.message })
    }
  },

  renameSession: async (id, title) => {
    try {
      await api.renameSession(id, title)
      set(st => ({ sessions: st.sessions.map(s => (s.id === id ? { ...s, title } : s)) }))
    } catch (e: any) {
      set({ error: e.message })
    }
  },

  removeSession: async (id) => {
    try {
      await api.deleteSession(id)
      const sessions = get().sessions.filter(s => s.id !== id)
      const wasCurrent = get().currentSessionId === id
      set({
        sessions,
        currentSessionId: wasCurrent ? (sessions[0]?.id ?? null) : get().currentSessionId,
        bubbles: wasCurrent ? [] : get().bubbles,
      })
      if (wasCurrent && get().currentSessionId) {
        await get().selectSession(get().currentSessionId!)
      }
    } catch (e: any) {
      set({ error: e.message })
    }
  },

  sendMessage: async (text) => {
    const sid = get().currentSessionId
    if (!sid || get().streaming) return
    const trimmed = text.trim()
    if (!trimmed) return

    const userBubble: ChatBubble = { id: newBubbleId(), role: 'user', content: trimmed }
    const assistantBubble: ChatBubble = { id: newBubbleId(), role: 'assistant', content: '', streaming: true }
    set(st => ({ bubbles: [...st.bubbles, userBubble, assistantBubble], streaming: true }))

    let currentAssistantId = assistantBubble.id
    const abort = new AbortController()
    set({ abortCtrl: abort })

    try {
      for await (const ev of api.streamChat({ message: trimmed, session_id: sid }, abort.signal)) {
        if (ev.type === 'title') {
          set(st => ({
            sessions: st.sessions.map(s => (s.id === ev.session_id ? { ...s, title: ev.title } : s)),
          }))
          continue
        }
        const prev = get().bubbles
        const { bubbles, nextCurrentId } = applyEvent(prev, currentAssistantId, ev)
        currentAssistantId = nextCurrentId
        set({ bubbles })

        if (ev.type === 'done') {
          set({ streaming: false })
          // 刷新会话列表 mtime + token 统计
          try {
            const [sess, toks] = await Promise.all([
              api.listSessions(),
              api.getSessionTokens(sid),
            ])
            set({ sessions: sess.sessions, sessionTokens: toks })
          } catch {
            /* 忽略：主流程已成功 */
          }
        } else if (ev.type === 'error') {
          set({ streaming: false, error: ev.error })
        }
      }
    } catch (e: any) {
      const aborted = e.name === 'AbortError'
      set(st => ({
        bubbles: st.bubbles.map(b =>
          b.id === currentAssistantId ? { ...b, streaming: false, error: aborted ? b.error : e.message } : b,
        ),
        streaming: false,
        error: aborted ? st.error : e.message,
      }))
    } finally {
      set({ abortCtrl: null })
    }
  },

  stopStreaming: () => {
    get().abortCtrl?.abort()
    set({ streaming: false })
  },

  toggleRag: async () => {
    if (get().ragToggling) return
    const next = !get().ragMode
    set({ ragMode: next, ragToggling: true }) // 乐观更新
    try {
      const r = await api.setRagMode(next)
      set({ ragMode: r.rag_mode, ragToggling: false })
    } catch (e: any) {
      set({ ragMode: !next, ragToggling: false, error: e.message }) // 回滚
    }
  },

  compressCurrent: async () => {
    const sid = get().currentSessionId
    if (!sid) return
    try {
      await api.compressSession(sid)
      const [hist, toks] = await Promise.all([api.getHistory(sid), api.getSessionTokens(sid)])
      if (get().currentSessionId !== sid) return
      set({ bubbles: historyToBubbles(hist.messages), sessionTokens: toks })
      const sess = await api.listSessions()
      set({ sessions: sess.sessions })
    } catch (e: any) {
      set({ error: e.message })
    }
  },

  toggleSidebar: () => set(st => ({ sidebarOpen: !st.sidebarOpen })),
  toggleInspector: () => set(st => ({ inspectorOpen: !st.inspectorOpen })),

  loadRawMessages: async (id) => {
    const sid = id ?? get().currentSessionId
    if (!sid) return
    set({ rawLoading: true })
    try {
      const m = await api.getRawMessages(sid)
      set({ rawMessages: m, rawLoading: false, rawOpen: true })
    } catch (e: any) {
      set({ rawLoading: false, error: e.message })
    }
  },
  setRawOpen: (open) => set({ rawOpen: open }),

  openFile: async (path) => {
    try {
      const f = await api.readFile(path)
      set(st => ({ editor: { ...st.editor, currentPath: path, content: f.content, savedContent: f.content } }))
      // 顺便拉该文件 token
      void get().refreshFileTokens([path])
    } catch (e: any) {
      set({ error: e.message })
    }
  },
  setEditorContent: (content) =>
    set(st => ({ editor: { ...st.editor, content } })),
  saveCurrentFile: async () => {
    const { editor } = get()
    if (!editor.currentPath) return
    set(st => ({ editor: { ...st.editor, saving: true } }))
    try {
      await api.saveFile(editor.currentPath, editor.content)
      set(st => ({ editor: { ...st.editor, saving: false, savedContent: st.editor.content } }))
      await get().refreshFileTokens([editor.currentPath])
    } catch (e: any) {
      set(st => ({ editor: { ...st.editor, saving: false }, error: e.message }))
    }
  },
  refreshFileTokens: async (paths) => {
    if (!paths.length) return
    try {
      const r = await api.getFilesTokens(paths)
      set(st => ({
        editor: {
          ...st.editor,
          fileTokens: {
            ...st.editor.fileTokens,
            ...Object.fromEntries(r.files.map(f => [f.path, f.tokens])),
          },
        },
      }))
    } catch {
      /* 静默 */
    }
  },
  clearError: () => set({ error: null }),
}))
