# Description

## 中文版

mini-OpenClaw 是复刻 OpenClaw 体验的本地透明 AI Agent 工作台，SKILL.md 说明书拖入即用。含金量在 Agent 工程化：双流解析统一逐字输出与工具调用为七类 SSE 事件；分段保存让每轮工具回合独立成消息、喂 LLM 时自动合并；MEMORY.md 全文注入与 RAG 检索一键切换，MD5 检测免重建索引；长对话前 50% LLM 压缩归档、摘要累积不丢；终端黑名单、目录白名单、路径穿越检查三道沙箱；六文件 System Prompt 每轮重读即生效。FastAPI 加 LangChain create_agent 加 DeepSeek 加 Next.js 构建，JSON 与 Markdown 持久化无数据库，适合作为个人 AI 副手的最小可读实现。

## English

mini-OpenClaw is a local, transparent AI Agent workbench that recreates the core experience of OpenClaw: file-first memory, skills as plugins — drop in a SKILL.md and the Agent teaches itself by reading it with read_file. Its engineering highlights: dual-stream event parsing (messages + updates) unifies token-by-token output and tool calls into seven SSE event types; a segment mechanism saves each tool round as a separate assistant message, then merges consecutive ones back for LLM-conformant alternation; MEMORY.md toggles between full-text injection and RAG vector retrieval in one click, with MD5 change detection skipping needless re-indexing; long conversations compress the first 50% into an archived LLM summary with accumulated compressed_context; three sandbox layers (terminal command blacklist, directory whitelist, path-traversal checks) keep the Agent in bounds; and the six-file System Prompt is re-assembled every turn so persona and memory edits take effect instantly. Built with FastAPI, LangChain 1.x create_agent, DeepSeek, LlamaIndex and Next.js 14; all state lives in human-readable JSON and Markdown with no database. Ideal as a minimal readable reference for building a personal AI companion.
