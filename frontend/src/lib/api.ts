/* ============================================================
   mini OpenClaw · API 客户端
   - 走 vite proxy（fetch '/api/...' → 后端 8002），免跨域
   - streamChat：fetch + ReadableStream 手写 SSE 解析
     （浏览器 EventSource 不支持 POST，必须手写）
   - 所有 data 行都是 JSON 字符串（含 new_response 的 {}），需 JSON.parse
   ============================================================ */
import type {
  SSEEvent, SessionMeta, SessionCreated, HistoryResponse,
  MessagesResponse, FileContent, FileSaveRequest, SkillItem,
  SessionTokens, FileTokens, CompressResult, RagMode,
} from '../types'

const API_BASE = '/api'

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText} ${txt}`.trim())
  }
  return res.json() as Promise<T>
}

/* ========== 会话 ========== */
export const listSessions = () =>
  fetch(`${API_BASE}/sessions`).then(json<{ sessions: SessionMeta[] }>)

export const createSession = () =>
  fetch(`${API_BASE}/sessions`, { method: 'POST' }).then(json<SessionCreated>)

export const renameSession = (id: string, title: string) =>
  fetch(`${API_BASE}/sessions/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  }).then(json<{ id: string; title: string }>)

export const deleteSession = (id: string) =>
  fetch(`${API_BASE}/sessions/${id}`, { method: 'DELETE' })
    .then(json<{ status: string; id: string }>)

export const getHistory = (id: string) =>
  fetch(`${API_BASE}/sessions/${id}/history`).then(json<HistoryResponse>)

export const getRawMessages = (id: string) =>
  fetch(`${API_BASE}/sessions/${id}/messages`).then(json<MessagesResponse>)

export const generateTitle = (id: string) =>
  fetch(`${API_BASE}/sessions/${id}/generate-title`, { method: 'POST' })
    .then(json<{ session_id: string; title: string }>)

export const compressSession = (id: string) =>
  fetch(`${API_BASE}/sessions/${id}/compress`, { method: 'POST' })
    .then(json<CompressResult>)

/* ========== 文件 / 技能 ========== */
export const readFile = (path: string) =>
  fetch(`${API_BASE}/files?path=${encodeURIComponent(path)}`).then(json<FileContent>)

export const saveFile = (path: string, content: string) =>
  fetch(`${API_BASE}/files`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, content } satisfies FileSaveRequest),
  }).then(json<{ path: string; status: string }>)

export const listSkills = () =>
  fetch(`${API_BASE}/skills`).then(json<{ skills: SkillItem[] }>)

/* ========== Token ========== */
export const getSessionTokens = (id: string) =>
  fetch(`${API_BASE}/tokens/session/${id}`).then(json<SessionTokens>)

export const getFilesTokens = (paths: string[]) =>
  fetch(`${API_BASE}/tokens/files`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
  }).then(json<{ files: FileTokens[] }>)

/* ========== RAG ========== */
export const getRagMode = () =>
  fetch(`${API_BASE}/config/rag-mode`).then(json<RagMode>)

export const setRagMode = (enabled: boolean) =>
  fetch(`${API_BASE}/config/rag-mode`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  }).then(json<RagMode>)

/* ========== 对话 SSE ========== */
export interface ChatRequest {
  message: string
  session_id: string
  stream?: boolean
}

/**
 * SSE 流式对话。async generator 逐事件 yield。
 *
 * sse_starlette 帧格式：
 *   event: <name>
 *   data: <JSON 字符串>
 *   <空行>  ← 事件边界
 *
 * data: 后允许一个前导空格（SSE 规范），解析时去掉。
 * 一个事件可跨多行 data:（按 \n 合并），本项目每事件单行 data，但仍按规范处理。
 */
export async function* streamChat(
  req: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent, void, unknown> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...req, stream: true }),
    signal,
  })
  if (!res.ok || !res.body) {
    const txt = await res.text().catch(() => '')
    throw new Error(`chat ${res.status}: ${txt}`.trim())
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let currentEvent = ''
  let dataLines: string[] = []

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // 按行切，最后可能是不完整行，留 buffer 等下次
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''

      for (const raw of lines) {
        const line = raw.replace(/\r$/, '')
        if (line === '') {
          // 空行 = 事件边界：dispatch
          if (currentEvent && dataLines.length) {
            const dataStr = dataLines.join('\n')
            let payload: any
            try {
              payload = dataStr === '' ? {} : JSON.parse(dataStr)
            } catch {
              payload = { raw: dataStr }
            }
            yield normalizeEvent(currentEvent, payload)
          }
          currentEvent = ''
          dataLines = []
          continue
        }
        if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          // data: 后去一个前导空格（SSE 规范允许）
          dataLines.push(line.slice(5).replace(/^ /, ''))
        }
        // 忽略 id:/retry:/comment 等其它 SSE 字段
      }
    }
    // 流结束时若残留一个未 dispatch 的事件，补发
    if (currentEvent && dataLines.length) {
      const dataStr = dataLines.join('\n')
      try {
        const payload = dataStr === '' ? {} : JSON.parse(dataStr)
        yield normalizeEvent(currentEvent, payload)
      } catch {
        /* 丢弃 */
      }
    }
  } finally {
    reader.releaseLock?.()
  }
}

function normalizeEvent(name: string, p: any): SSEEvent {
  switch (name) {
    case 'retrieval':
      return { type: 'retrieval', query: p.query ?? '', results: p.results ?? [] }
    case 'token':
      return { type: 'token', content: p.content ?? '' }
    case 'new_response':
      return { type: 'new_response' }
    case 'tool_start':
      return { type: 'tool_start', tool: p.tool ?? '', input: p.input ?? '' }
    case 'tool_end':
      return { type: 'tool_end', tool: p.tool ?? '', output: p.output ?? '' }
    case 'done':
      return { type: 'done', content: p.content ?? '', session_id: p.session_id ?? '' }
    case 'title':
      return { type: 'title', session_id: p.session_id ?? '', title: p.title ?? '' }
    case 'error':
      return { type: 'error', error: p.error ?? '未知错误' }
    default:
      return { type: 'error', error: `未知事件: ${name}` }
  }
}
