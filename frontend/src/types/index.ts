/* ============================================================
   mini OpenClaw · 前端类型定义
   按 backend API 契约（17 端点 + SSE 八类事件）逐字对齐
   ============================================================ */

/* ---------- SSE 事件（八类） ---------- */
export interface RetrievalResult {
  score: number
  text: string
}

export type SSEEvent =
  | { type: 'retrieval'; query: string; results: RetrievalResult[] }
  | { type: 'token'; content: string }
  | { type: 'new_response' }
  | { type: 'tool_start'; tool: string; input: string }
  | { type: 'tool_end'; tool: string; output: string }
  | { type: 'done'; content: string; session_id: string }
  | { type: 'title'; session_id: string; title: string }
  | { type: 'error'; error: string }

/* ---------- 会话 ---------- */
export interface SessionMeta {
  id: string
  title: string
  updated_at: number // Unix 秒（浮点）
}
export interface SessionCreated {
  id: string
  title: string
  created_at: number
  updated_at: number
}

/* ---------- 消息（磁盘存储格式） ---------- */
export interface ToolCall {
  tool: string
  input: string
  output?: string
}
export interface StoredMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  tool_calls?: ToolCall[]
}
export interface HistoryResponse {
  session_id: string
  messages: StoredMessage[] // 不含 System Prompt，分段保留
}
export interface MessagesResponse {
  session_id: string
  title: string
  messages: StoredMessage[] // 首条恒为 system（完整 System Prompt）
}

/* ---------- 文件 / 技能 ---------- */
export interface FileContent {
  path: string
  content: string
}
export interface FileSaveRequest {
  path: string
  content: string
}
export interface SkillItem {
  name: string
  path: string
  description: string
}

/* ---------- Token ---------- */
export interface SessionTokens {
  system_tokens: number
  message_tokens: number
  compressed_tokens?: number
  total_tokens: number
}
export interface FileTokens {
  path: string
  tokens: number
}

/* ---------- 压缩 / RAG ---------- */
export interface CompressResult {
  archived_count: number
  remaining_count: number
}
export interface RagMode {
  rag_mode: boolean
}

/* ---------- 前端气泡状态（派生自 SSE / history） ---------- */
export interface BubbleToolCall {
  tool: string
  input: string
  output?: string
  status: 'running' | 'done'
}
export interface ChatBubble {
  id: string
  role: 'user' | 'assistant'
  content: string
  toolCalls?: BubbleToolCall[]
  retrieval?: { query: string; results: RetrievalResult[] }
  streaming?: boolean
  error?: string
}

/* ---------- 工具元数据（思考链配色） ---------- */
export const TOOL_META: Record<
  string,
  { label: string; colorVar: string; icon: 'terminal' | 'python' | 'fetch' | 'readfile' | 'search' }
> = {
  terminal: { label: '终端', colorVar: '--color-tool-terminal', icon: 'terminal' },
  python_repl: { label: 'Python', colorVar: '--color-tool-python', icon: 'python' },
  fetch_url: { label: '网页抓取', colorVar: '--color-tool-fetch', icon: 'fetch' },
  read_file: { label: '文件读取', colorVar: '--color-tool-readfile', icon: 'readfile' },
  search_knowledge_base: { label: '知识库检索', colorVar: '--color-tool-search', icon: 'search' },
}
