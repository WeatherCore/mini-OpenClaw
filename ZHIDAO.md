# 📖 mini OpenClaw 项目导读指南

> 本文件是 `mini-OpenClaw` 项目的中文导读，帮助你从零开始理解这个轻量级透明 AI Agent 系统的架构、代码和运行方式。

---

## 目录

1. [这个项目是干什么的？](#1-这个项目是干什么的)
2. [核心概念速览](#2-核心概念速览)
3. [项目目录结构详解](#3-项目目录结构详解)
4. [运行流程全景图](#4-运行流程全景图)
5. [逐文件代码导读](#5-逐文件代码导读)
6. [关键设计模式解析](#6-关键设计模式解析)
7. [配置系统详解](#7-配置系统详解)
8. [如何运行和测试](#8-如何运行和测试)
9. [复刻建议与学习路线](#9-复刻建议与学习路线)
10. [常见问题](#10-常见问题)

---

## 1. 这个项目是干什么的？

**一句话总结**：mini OpenClaw 是一个跑在本地的透明 AI Agent 工作台——你跟它对话，它能调用工具（终端/Python/网页/文件/知识库）干活，而它"是怎么想的、读了什么记忆、调了什么工具"，全部以人类可读的文件和事件流摊开给你看。

**更具体地说**：

```
用户提问 → 拼 System Prompt（6 个 Markdown 文件）→ DeepSeek 推理
    → 需要干活就调工具（读 SKILL.md 学技能 → 组合执行）
    → 全程 SSE 事件流推给前端（逐字输出 + 工具调用 + 检索结果）
    → 会话/记忆落盘为本地 JSON / Markdown 文件
```

它**不是一个聊天机器人 Demo**，而是一个 **"文件即记忆"的数字副手骨架**（PRD 原文定位：复刻并优化 OpenClaw 的核心体验）：

| 设计支柱 | 含义 |
|---|---|
| 🗂️ 文件即记忆 | 不引入数据库/向量库黑盒，记忆躺在 `memory/MEMORY.md`，会话躺在 `sessions/*.json`，打开就能看 |
| 🧩 技能即插件 | 遵循 Anthropic Agent Skills 范式：一个文件夹 + 一份 `SKILL.md` 说明书就是一项能力，拖入即用，零代码注册 |
| 🔍 全程透明 | System Prompt 六文件拼接逻辑、工具调用链、记忆读写对开发者全可见，前端还有 Raw Messages 面板实时审视 |

---

## 2. 核心概念速览

读代码前，先弄懂这 6 个概念：

### 2.1 Agent 与 create_agent（LangChain 1.x）

Agent = LLM + 工具。LLM 决定调哪个工具、传什么参数；工具执行完把结果喂回 LLM，循环往复直到产出最终回答（ReAct 模式）。

本项目按 PRD 强制使用 **LangChain 1.x 的 `create_agent`**（`from langchain.agents import create_agent`）——它是 LangChain 0.2 时代 `AgentExecutor` / `create_react_agent` 的替代品，底层跑在 LangGraph 运行时上，但接口更简洁。**严禁**在二次开发中退回旧版链式结构。

```python
# graph/agent.py L42 附近
agent = create_agent(model=self._llm, tools=self._tools, system_prompt=system_prompt)
```

把它理解为：**招了一个会自己挑工具的员工，工具箱和岗位说明书（system prompt）入职时一次性发齐**。

### 2.2 SSE（Server-Sent Events）与事件流

后端把 Agent 的一举一动拆成 7 类事件，通过 HTTP 长连接逐条推给前端：

| 事件 | 何时出现 | 前端行为 |
|---|---|---|
| `retrieval` | RAG 模式下检索到记忆片段 | 渲染紫色 Memory Retrieval 卡片 |
| `token` | LLM 每产出一个字 | 追加到当前气泡（打字机效果） |
| `new_response` | 一轮工具调用结束、新回答开始 | **新开一个 assistant 气泡** |
| `tool_start` / `tool_end` | 工具调用开始/结束 | 思考链（ThoughtChain）追加卡片、转圈→打勾 |
| `done` | 本轮全部完成 | 结束流式状态 |
| `title` | 首轮对话自动生成标题 | 更新侧边栏会话名 |
| `error` | 任何异常 | 气泡内追加错误提示 |

注意：浏览器原生 `EventSource` 只支持 GET，而对话是 POST——所以前端 `lib/api.ts` 手写了一个基于 `fetch` + `ReadableStream` 的 SSE 解析器。

### 2.3 System Prompt 六文件拼接

每次对话前，`graph/prompt_builder.py` 把 6 个 Markdown 文件按固定顺序拼成 System Prompt（单个组件超 20000 字符截断）：

| 顺序 | 文件 | 角色 |
|---|---|---|
| 1 | `SKILLS_SNAPSHOT.md` | 技能清单（启动时自动生成） |
| 2 | `workspace/SOUL.md` | 人格、语气、边界 |
| 3 | `workspace/IDENTITY.md` | 名字、风格、签名 emoji |
| 4 | `workspace/USER.md` | 用户画像 |
| 5 | `workspace/AGENTS.md` | 行为准则 + 技能调用协议 + 记忆协议 |
| 6 | `memory/MEMORY.md` | 跨会话长期记忆（RAG 模式下**不拼**，改为检索注入） |

**改 Agent 性格 = 编辑 SOUL.md；让它记住你 = 编辑 USER.md/MEMORY.md。一切皆文件。**

### 2.4 Instruction-following 技能系统（本项目最独特的设计）

Skills **不是**预写好的 Python 函数，而是**教 Agent 使用基础工具的说明书**：

```
① 感知：Agent 在 System Prompt 里看到 <available_skills> 技能快照
② 决策：用户说"查询北京天气" → Agent 发现 get_weather 技能匹配
③ 行动：不调用 get_weather()（它不存在！），
        而是调用 read_file("skills/get_weather/SKILL.md") 读说明书
④ 执行：按说明书用 fetch_url / python_repl 等核心工具组合完成任务
```

启动时 `tools/skills_scanner.py` 扫描 `skills/*/SKILL.md` 的 YAML frontmatter，生成 XML 风格的 `SKILLS_SNAPSHOT.md` 注入提示词。

### 2.5 记忆双模式（全文注入 vs RAG 检索）

| 模式 | MEMORY.md 怎么进上下文 | 开关 |
|---|---|---|
| 关（默认） | 整个文件直接拼进 System Prompt | `GET/PUT /api/config/rag-mode` |
| 开（RAG） | 用 LlamaIndex 把 MEMORY.md 切块向量化，按用户问题检索 top-3 片段，以 `[记忆检索结果]` 注入对话历史 | 侧边栏 🗄️ 按钮一键切换 |

索引用 **MD5 变更检测**：MEMORY.md 内容没变就直接复用 `storage/memory_index/` 里的持久化索引，变了才重建（`graph/memory_indexer.py`）。保存 MEMORY.md 时（Monaco 编辑器 Ctrl+S），`api/files.py` 会自动触发索引重建。

### 2.6 会话分段保存与合并（Segment 机制）

一轮对话里 Agent 可能先调工具、再回答，再调工具、再回答。后端把**每段回答存成一条独立的 assistant 消息**（各自携带自己的 tool_calls），前端因此能把每段回答渲染成独立气泡；而喂给 LLM 时，`session_manager.load_session_for_agent()` 又把**连续 assistant 消息合并回一条**，保证 user/assistant 严格交替——两全其美。

---

## 3. 项目目录结构详解

```
mini-OpenClaw/
│
├── backend/                        # ⭐ FastAPI + LangChain Agent 后端（端口 8002）
│   ├── app.py                      # ⭐ 入口：lifespan 启动初始化 + 挂载 6 个路由
│   ├── config.py                   # 🔧 JSON 配置持久化（rag_mode 开关）
│   ├── config.json                 # 配置落盘文件（rag_mode: false）
│   ├── .env.example                # 环境变量模板（DeepSeek/Embedding Key）
│   ├── requirements.txt            # Python 依赖清单
│   ├── test_openweather.py         # 🧪 天气技能联调脚本（测试，跳过注释）
│   │
│   ├── api/                        # 🔌 REST 路由层（前台总服务台，只收单转交）
│   │   ├── chat.py                 #   POST /chat：SSE 流式对话 + 分段保存 + 自动起名
│   │   ├── sessions.py             #   会话 CRUD + 原始消息 + AI 生成标题
│   │   ├── files.py                #   文件读写（Monaco 编辑器后端）+ 技能列表
│   │   ├── tokens.py               #   Token 统计（tiktoken cl100k_base）
│   │   ├── compress.py             #   对话历史压缩（前 50% 摘要归档）
│   │   └── config_api.py           #   RAG 模式开关接口
│   │
│   ├── graph/                      # 🧠 Agent 编排层（业务经理）
│   │   ├── agent.py                #   AgentManager：create_agent + 双流事件解析
│   │   ├── prompt_builder.py       #   System Prompt 六文件拼接（20k 截断）
│   │   ├── session_manager.py      #   JSON 会话持久化 + v1→v2 迁移 + 压缩/合并
│   │   └── memory_indexer.py       #   MEMORY.md 向量索引（MD5 变更检测）
│   │
│   ├── tools/                      # 🛠️ 5 大核心工具（Agent 的手）
│   │   ├── __init__.py             #   get_all_tools 工厂
│   │   ├── terminal_tool.py        #   沙箱终端（黑名单 + 30s 超时）
│   │   ├── python_repl_tool.py     #   Python 解释器（LangChain 原生封装）
│   │   ├── fetch_url_tool.py       #   网页抓取 → Markdown 清洗
│   │   ├── read_file_tool.py       #   沙箱文件读取（技能机制的依赖）
│   │   ├── search_knowledge_tool.py#   knowledge/ 知识库 LlamaIndex 检索
│   │   └── skills_scanner.py       #   扫描 SKILL.md 生成技能快照
│   │
│   ├── workspace/                  # 📋 System Prompt 组成文件（SOUL/IDENTITY/USER/AGENTS）
│   ├── skills/                     # 📋 Agent Skills（get_weather 等，SKILL.md 说明书）
│   ├── memory/                     # 🧠 长期记忆（MEMORY.md）
│   ├── sessions/                   # 💾 JSON 会话记录（session-*.json）
│   ├── SKILLS_SNAPSHOT.md          # 自动生成的技能快照
│   └── memory_hex.txt              # 调试产物（十六进制 dump）
│
├── frontend/                       # ⭐ Next.js 14 前端（端口 3000）
│   ├── package.json                # next 14 + react 18 + monaco + react-markdown
│   ├── tailwind.config.ts          # 浅色 Apple 风格主题变量
│   └── src/
│       ├── app/                    #   layout.tsx（根布局）+ page.tsx（三栏主布局）
│       ├── lib/
│       │   ├── api.ts              #   API 客户端 + 手写 POST SSE 解析器
│       │   └── store.tsx           # ⭐ 全局状态中枢（Context + SSE 事件分发）
│       └── components/
│           ├── chat/               #   ChatPanel/ChatMessage/ChatInput/ThoughtChain/RetrievalCard
│           ├── editor/             #   InspectorPanel（Monaco 文件编辑器）
│           └── layout/             #   Navbar/Sidebar/ResizeHandle
│
├── Mini-OpenClaw 开发需求文档 (PRD).pdf   # 📋 产品需求文档
├── ZHIDAO.md                       # 📖 本文件
├── README.md                       # 项目门面
└── Description.md                  # 项目名片（中英双版）
```

---

## 4. 运行流程全景图

### 4.1 启动阶段（app.py lifespan）

```
┌────────────────────────────────────────────────────────────────┐
│ uvicorn app:app --port 8002 启动                                │
│                                                                │
│ lifespan(app) 依次执行三步初始化（任何一步失败服务起不来）：        │
│  ① scan_skills(BASE_DIR)                                       │
│     扫描 skills/*/SKILL.md frontmatter → 生成 SKILLS_SNAPSHOT.md│
│  ② agent_manager.initialize(BASE_DIR)                          │
│     组装 5 大工具 + ChatDeepSeek(DeepSeek) + session_manager     │
│  ③ memory_indexer.rebuild_index()                              │
│     MEMORY.md 切块向量化 → 持久化到 storage/memory_index/        │
│                                                                │
│ 退出条件：打印 "✅ mini OpenClaw backend ready" → yield 放行请求  │
└────────────────────────────────────────────────────────────────┘
```

### 4.2 一次对话的完整生命周期（核心链路）

```
┌──────────────────────────────────────────────────────────────────┐
│ 用户在 ChatInput 输入 → store.sendMessage(text)                    │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│ 前端：POST /api/chat {message, session_id, stream:true}            │
│ streamChat() 手写 SSE 解析器逐行解析 event:/data:                  │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│ 后端：api/chat.py event_generator()                               │
│ ① load_session_for_agent() 读历史（合并连续 assistant + 注入压缩摘要）│
│ ② agent_manager.astream(message, history) ↓                      │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│ graph/agent.py astream()（每轮请求重新 _build_agent，重读全部文件）   │
│ ├─ RAG 开？→ memory_indexer.retrieve() → yield retrieval 事件      │
│ │   并把检索片段拼成 "[记忆检索结果]" 塞进历史尾部                    │
│ ├─ create_agent(model, tools, system_prompt)                      │
│ └─ agent.astream(stream_mode=["messages","updates"]) 双流并行解析：  │
│     messages 流 → AIMessageChunk 逐字 → yield token                │
│     updates 流 → model 节点 tool_calls → yield tool_start          │
│                → tools 节点结果    → yield tool_end                 │
│     工具轮结束后的第一个新 token → yield new_response（分段边界）     │
│ 退出条件：流耗尽 → yield done（携带完整回答）                        │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│ 回到 event_generator：done 时批量落盘                               │
│  save_message(user) 一次 + 每个分段各存一条 assistant（带 tool_calls）│
│  首轮对话？→ _generate_title()（DeepSeek 生成 ≤10 字标题）→ title 事件│
│  任何异常 → yield error 事件（不打断连接）                           │
└──────────────────────────────────────────────────────────────────┘
```

### 4.3 记忆压缩链路（用户点侧边栏 🔧 按钮）

```
前端 compressCurrentSession() → POST /sessions/{id}/compress
  → api/compress.py：消息 < 4 条 → 400 拒绝
  → 取前 50%（至少 4 条）→ DeepSeek 生成 ≤500 字中文摘要
  → session_manager.compress_history()：
      原消息归档到 sessions/archive/{id}_{ts}.json
      主文件只留后半段，compressed_context 追加摘要（多次压缩用 --- 分隔）
  → 前端刷新聊天区与 Token 统计
```

**要点**：压缩后的摘要会在**下一次对话**时被 `load_session_for_agent()` 以 `[以下是之前对话的摘要]` 的 assistant 消息注入头部——长会话不爆 Token，记忆不丢。

---

## 5. 逐文件代码导读

### 5.0 阅读顺序建议（数据驱动，来自依赖图分析）

骨架分析给出的"快速上手 3 文件"（入度/出度自动计算）：

| 顺序 | 文件 | 角色 | 为什么先读 |
|---|---|---|---|
| 1 | `frontend/src/components/chat/ChatMessage.tsx` | 入口 | 入度 0 出度 1：顶层不被依赖，读者最先接触的渲染单元，126 行半小时能读完 |
| 2 | `frontend/src/lib/store.tsx` | 中间 | 入度 4：入口直接依赖里入度最高，是理解前端一切的枢纽（SSE 事件分发全在这） |
| 3 | `backend/api/chat.py` | 补充 | 前端事件的"生产者"，对照读立即打通前后端 |

后端推荐顺序（从工具到编排，先易后难）：

```
第 1 步 config.py + prompt_builder.py    → 配置与提示词（约 20 分钟）
第 2 步 tools/*.py（7 个小文件）          → 5 大工具 + 技能扫描（约 40 分钟）
第 3 步 graph/session_manager.py         → 会话持久化（约 40 分钟）
第 4 步 graph/memory_indexer.py          → 记忆索引（约 30 分钟）
第 5 步 graph/agent.py                   → Agent 编排核心（约 50 分钟）
第 6 步 api/*.py                         → 路由层收口（约 40 分钟）
```

### 5.1 后端 · 入口与配置

#### `backend/app.py`（62 行）— FastAPI 入口

- **作用**：定义 lifespan 启动钩子（技能扫描 → Agent 初始化 → 记忆索引构建），挂 CORS 中间件，注册 6 个 `/api` 前缀路由。
- **关键点**：lifespan 里的 import 全部**延迟到函数内**（L24-26）——避免循环依赖，也加快冷启动。

**要点**：所有路由统一 `/api` 前缀；CORS 全放开（本地开发友好，部署时应收紧）。

#### `backend/config.py`（43 行）— JSON 配置管理

- **作用**：`config.json` 的读写封装，目前只有 `rag_mode` 一个配置项。
- **关键函数**：`load_config()`（L14）文件缺失/损坏时回退默认值；`set_rag_mode()`（L38）读-改-写三步。

**要点**：读配置永远带默认值兜底，坏配置不会让服务崩；无缓存，每次读盘（配置项少，可接受）。

### 5.2 后端 · api/ 路由层

#### `backend/api/chat.py`（189 行）— SSE 对话（最核心路由）

- **作用**：`POST /chat`，把 Agent 双流事件翻译成 SSE 事件并落盘。
- **关键函数**：

| 函数 | 行号 | 职责 |
|---|---|---|
| `_generate_title` | L24 | 取首轮 200 字摘要让 DeepSeek 生成 ≤10 字标题，失败返回 None 不打扰主流程 |
| `event_generator` | L66 | 核心生成器：段（segment）跟踪、7 类事件分发、done 时批量落盘、首轮自动起名 |
| `chat` | L181 | 路由入口：stream=true 走 SSE，否则 `ainvoke` 一次性返回 |

- **关键数据结构**：`current_segment = {"content": "", "tool_calls": []}`——每收到 `new_response` 就封存上一段、开启新段。

**要点**：① 用户消息只在 done 时存一次，assistant 按分段各存一条；② `tool_end` 匹配用的是"逆序找同名且无 output 的调用"——与前端 store.tsx 的逆序匹配逻辑完全镜像。

#### `backend/api/sessions.py`（124 行）— 会话 CRUD

- **作用**：列表/新建/重命名/删除/原始消息/历史/AI 起名 7 个端点。
- **关键点**：`create_session`（L34）生成 `session-{uuid12}`；`get_raw_messages`（L59）把 System Prompt 拼在消息头部返回（Raw Messages 面板数据源）；`generate_title`（L76）与 chat.py 的 `_generate_title` 逻辑重复（已知 DRY 债务），但它带**降级兜底**：LLM 失败时用用户消息前 10 字当标题。

#### `backend/api/files.py`（103 行）— Monaco 编辑器文件桥

- **作用**：`GET/POST /files` 读写文件 + `GET /skills` 技能列表。
- **安全设计**（本项目文件安全的核心）：
  - `_validate_path`（L20）：目录白名单 `workspace/ memory/ skills/ knowledge/` + 根文件白名单 `SKILLS_SNAPSHOT.md`；
  - `resolve()` 后必须仍以 `BASE_DIR` 开头（防 `../` 路径穿越）；
  - 保存 `memory/MEMORY.md` 成功后**自动触发记忆索引重建**（L49-52）——前端编辑记忆 → RAG 索引实时生效的闭环。
- **关键点**：`list_skills`（L69）解析 SKILL.md 的 YAML frontmatter，供右侧面板技能列表。

#### `backend/api/tokens.py`（69 行）— Token 记账

- **作用**：会话级（System Prompt + 全部消息）与文件级 Token 统计，用 tiktoken `cl100k_base`（模块级缓存 encoder 实例）。
- **要点**：会话统计只算消息 content，不算 tool_calls 内容与压缩摘要——**侧边栏显示的是保守下限**。

#### `backend/api/compress.py`（72 行）— 对话压缩

- **作用**：`POST /sessions/{id}/compress`：≥4 条消息才可压；取前 50%（`max(4, len//2)`，至少 4 条）→ DeepSeek 摘要 → `compress_history` 落盘。
- **要点**：摘要提示词要求"保留关键信息、决策和结论，≤500 字"。

#### `backend/api/config_api.py`（24 行）— RAG 开关

- **作用**：`GET/PUT /config/rag-mode`，薄封装 `config.py` 的 get/set。前端侧边栏 🗄️ 按钮的数据源。

### 5.3 后端 · graph/ 编排层

#### `backend/graph/agent.py`（184 行）— AgentManager（Agent 之心）

- **作用**：单例 `agent_manager`。持有一个 DeepSeek LLM 实例 + 5 个工具，把 LangChain 事件流翻译成前端友好的 7 类事件。
- **关键方法**：

| 方法 | 行号 | 职责 |
|---|---|---|
| `initialize` | L23 | 启动时装配工具 + ChatDeepSeek（temperature=0.7，streaming=True） |
| `_build_agent` | L42 | **每次请求都重建 agent**——重读 SOUL/MEMORY 等文件，实现"改文件立即生效，无需重启" |
| `_build_messages` | L59 | 历史字典 → LangChain HumanMessage/AIMessage 序列 |
| `astream` | L72 | 双流解析（见 4.2 图），`tools_just_finished` 标志位捕捉分段边界 |
| `ainvoke` | L166 | 非流式兜底：一次性调用并从结果里倒序找首条 AI 消息 |

- **关键设计**：`stream_mode=["messages", "updates"]` 双流——`messages` 流逐 token（打字机），`updates` 流按节点给工具事件（model 节点的 tool_calls → tool_start；tools 节点的结果 → tool_end）。RAG 检索片段以 assistant 身份**追加在历史末尾**（不污染 System Prompt）。

**要点**：`astream` 里 `new_response` 事件的判定：`updates` 流发现 tools 节点跑完置位 `tools_just_finished=True`，下一个非工具的 token 到达时 yield `new_response` 并复位——这就是"分段边界"的信号源。

#### `backend/graph/session_manager.py`（243 行）— 会话持久化（后端最大文件）

- **作用**：单例 `session_manager`。`sessions/*.json` 的读写迁移压缩合并全套。
- **存储格式 v2**：`{title, created_at, updated_at, messages: [{role, content, tool_calls?}], compressed_context?}`；`_read_file`（L33）自动把 v1（裸数组）迁移成 v2。
- **关键方法**：

| 方法 | 行号 | 职责 |
|---|---|---|
| `_session_path` | L28 | session_id 过滤成 `[A-Za-z0-9_-]`，防文件名注入 |
| `compress_history` | L153 | 前半段归档到 `sessions/archive/`，主文件留后半 + `compressed_context` 累积摘要 |
| `load_session_for_agent` | L198 | 压缩摘要置顶注入 + 连续 assistant 合并 + 剥离 tool_calls |
| `list_sessions` | L129 | 按 mtime 倒序，坏文件降级用文件名当标题 |

**要点**：`load_session_for_agent` 是"存与用不对称"的枢纽——**存**的时候按分段拆开（方便前端渲染），**用**的时候合并回去（满足 LLM 的 user/assistant 交替规范）。

#### `backend/graph/memory_indexer.py`（166 行）— 记忆向量索引

- **作用**：单例。MEMORY.md → LlamaIndex 向量索引，MD5 变更检测。
- **关键链路**：`_maybe_rebuild`（L41）每次 retrieve 前对比 MD5 → 变了 `rebuild_index`（L48：SentenceSplitter chunk_size=256 overlap=32 → 向量化 → 持久化 `storage/memory_index/`）→ `_load_index`（L99）优先复用持久化索引。
- **要点**：Embedding 走 OpenAI 兼容接口（默认 text-embedding-3-small），`OPENAI_BASE_URL` 默认指向一个代理地址；LlamaIndex 未装全时静默降级（索引为 None，RAG 检索返回空列表）。

#### `backend/graph/prompt_builder.py`（61 行）— System Prompt 装配线

- **作用**：`build_system_prompt`（L23）按 §2.3 顺序拼接 6 文件；单组件超 `MAX_COMPONENT_LENGTH=20000` 截断并加 `...[truncated]` 标记；缺失文件静默跳过。
- **要点**：RAG 模式下 MEMORY.md 不拼接，改为追加 `RAG_GUIDANCE` 说明"记忆将以 [记忆检索结果] 呈现"。

### 5.4 后端 · tools/ 工具层

#### `backend/tools/__init__.py`（24 行）

- **作用**：`get_all_tools(base_dir)` 工厂，一次性装配 5 大工具返回给 AgentManager。

#### `backend/tools/terminal_tool.py`（79 行）— 沙箱终端

- **作用**：12 条高危命令黑名单（`rm -rf /`、fork 炸弹、shutdown 等）+ `cwd=root_dir` 锁定工作目录 + 30s 超时 + 5000 字符截断。
- **要点**：黑名单是**子串匹配**——绕过手法存在（如变量拼接），这是纵深防御的一层而非唯一防线；输出统一带 `[stderr]` 段。

#### `backend/tools/python_repl_tool.py`（18 行）— Python 解释器

- **作用**：直接包装 LangChain experimental 的 `PythonREPLTool`，改名为 `python_repl`，提示 Agent 用 `print()` 看输出。

#### `backend/tools/fetch_url_tool.py`（60 行）— 网页抓取

- **作用**：requests GET（15s 超时，自定义 UA）→ JSON 直接返回 / HTML 用 html2text 清洗成 Markdown（保留链接、丢图片、不折行）→ 5000 字符截断。
- **要点**：HTML→Markdown 清洗是 PRD 明确要求的增强——原始 HTML Token 消耗巨大。

#### `backend/tools/read_file_tool.py`（54 行）— 沙箱文件读取

- **作用**：技能机制的**核心依赖**（Agent 靠它读 SKILL.md）。路径归一化 + `resolve()` 后必须在项目根内（防穿越）+ 10000 字符截断。
- **要点**：错误信息以 `❌` 开头返回给 LLM——Agent 能读懂失败原因并自行调整。

#### `backend/tools/search_knowledge_tool.py`（103 行）— 知识库检索

- **作用**：`knowledge/` 目录 → LlamaIndex 索引（优先复用 `storage/` 持久化）→ `as_query_engine(similarity_top_k=3)` 检索合成 → 5000 字符截断。
- **[注意]** 工具描述写的是 hybrid retrieval（BM25+向量），**当前实现实际是向量 query_engine 检索 + LLM 合成**，PRD 要求的 BM25 混合检索尚未落地——已在源码注释中标注。

#### `backend/tools/skills_scanner.py`（49 行）— 技能快照生成器

- **作用**：`rglob("SKILL.md")` 递归扫描 → 解析 frontmatter → 生成 XML 风格 `<available_skills>` 快照写入 `SKILLS_SNAPSHOT.md`。
- **要点**：location 字段是 `./backend/skills/<name>/SKILL.md` 相对路径，Agent 拿它直接喂给 read_file。

### 5.5 前端 · lib/

#### `frontend/src/lib/api.ts`（257 行）— API 客户端

- **作用**：15 个 API 的 fetch 封装 + **手写 POST SSE 解析器** `streamChat`（L20）。
- **关键机制**：`fetch` → `response.body.getReader()` → 按 `\n` 切行 → `event:`/`data:` 前缀解析 → JSON.parse 后 yield；**半行缓冲**（`buffer`）保证跨 chunk 的行不被截断；空行重置事件类型。
- **要点**：`API_BASE` 用 `window.location.hostname:8002`——前端跑在哪个主机，后端就找哪个主机（局域网访问也能用）。

#### `frontend/src/lib/store.tsx`（524 行）— 全局状态中枢（前端最大文件）

- **作用**：React Context 单一 Store：消息流、会话管理、面板开合、压缩、RAG 开关。
- **关键机制**：
  - `sendMessage`（L318）：SSE 事件驱动状态更新。`currentAssistantIdRef` 指向"当前正在填充的气泡"，`new_response` 事件到来就新开气泡并更新 ref——**与后端分段机制一一对应**；
  - `tool_end` 用逆序查找第一个同名且 `status==="running"` 的调用打上 output（镜像后端逻辑）；
  - 历史加载（L161）与压缩后刷新（L254）各有一份"后端消息 → 前端气泡"的映射代码（已知重复，两处同步维护）；
  - RAG 切换是**乐观更新**：先改 UI，API 失败再回滚。
- **要点**：整个前端没有引入任何状态管理库，Context + useState + useCallback 裸写。

### 5.6 前端 · app/ 与 components/

#### `frontend/src/app/page.tsx`（91 行）— 三栏主布局

- **作用**：`Navbar` + `Sidebar｜ChatPanel｜InspectorPanel` 三栏 flex 布局，`ResizeHandle` 拖拽调宽（左栏下限 200px、右栏 280px、中间 360px）。
- **要点**：`Home` 只做一件事——`AppProvider` 包住 `MainLayout`，状态与视图彻底分离。

#### `frontend/src/app/layout.tsx`（20 行）

- **作用**：Next.js 根布局，注入 metadata 与 `globals.css`，`lang="zh-CN"`。

#### `frontend/src/components/chat/`（5 个文件）

| 文件 | 行数 | 职责 |
|---|---|---|
| `ChatPanel.tsx` | 63 | 消息列表滚动容器 + 空状态引导（快捷提问按钮）+ 自动滚底 |
| `ChatMessage.tsx` | 126 | 单气泡：用户右对齐 / assistant 左对齐；Markdown 渲染（react-markdown + GFM）；**401 正则识别 → 渲染专属 API Key 排障卡片**；空内容显示打字动画 |
| `ChatInput.tsx` | 55 | 输入框：Enter 发送 / Shift+Enter 换行、自动增高（上限 160px）、流式/压缩中禁用 |
| `ThoughtChain.tsx` | 81 | 思考链：每个工具一张可折叠卡片（5 种工具图标/配色），running 转圈 → done 打勾，展开看 Input/Output |
| `RetrievalCard.tsx` | 55 | RAG 检索结果卡片：来源 + 相似度分数 + 片段内容，可折叠 |

#### `frontend/src/components/editor/InspectorPanel.tsx`（355 行）— 右侧检查器

- **作用**：Memory/Skills 双 Tab。文件列表（6 个 workspace/memory 文件 + 技能列表）→ Monaco 编辑器查看/编辑 → Ctrl+S 保存（dirty 圆点提示、保存状态徽标）→ 全屏展开模式。
- **要点**：`MonacoEditor` 用 `next/dynamic` ssr:false 懒加载；文件列表每项带 Token 计数徽标（`getFileTokenCounts` 批量拉取）。

#### `frontend/src/components/layout/`（3 个文件）

| 文件 | 行数 | 职责 |
|---|---|---|
| `Navbar.tsx` | 66 | 顶栏：左栏开关（克莱因蓝）/ 品牌区（链接赋范空间）/ 右栏开关（活力橙） |
| `Sidebar.tsx` | 426 | 会话列表（重命名/删除/选中态）+ Raw Messages 折叠区（Token 统计、RAG 开关、压缩确认弹窗、全屏查看） |
| `ResizeHandle.tsx` | 62 | 6px 拖拽条：mousedown 锁 cursor、增量 delta 上抛；`onResizeRef` 防 stale closure |

---

## 6. 关键设计模式解析

### 6.1 分段保存 / 合并机制（Segment Pattern）

```
存储层（sessions/*.json）                上下文层（喂给 LLM）
[user]                                  [user]
[assistant 期1 + tool_calls]    ──合并──▶ [assistant 期1+期2+期3 拼接]
[assistant 期2]                 load_session_for_agent()
[assistant 期3 + tool_calls]
```

- **意图**：前端要"一轮多气泡"（每个工具回合后开新气泡），LLM 要严格 user/assistant 交替——两个矛盾需求用"存拆用合"化解。
- **代码落点**：拆在 `api/chat.py event_generator`（L220-227 封存分段），合在 `graph/session_manager.py load_session_for_agent`（L198）。

### 6.2 双流事件解析（messages + updates）

```python
async for event in agent.astream({"messages": messages}, stream_mode=["messages", "updates"]):
    mode, data = event            # 多模式下事件是 (mode, data) 元组
    if mode == "messages": ...     # 逐 token → yield {"type": "token"}
    elif mode == "updates": ...    # 节点级工具事件 → tool_start / tool_end
```

- **意图**：一次订阅同时拿到"字级体验"（打字机）和"节点级结构"（工具边界），不用自己攒状态机猜。

### 6.3 每请求重建 Agent（热更新 System Prompt）

`_build_agent()` 每轮对话都重新 `build_system_prompt()`（重读 6 个文件）+ `create_agent()`。代价是每轮多几次文件 IO（本地文件，微秒级），收益是**改 SOUL.md / MEMORY.md / 加技能立即可见，无需重启服务**。

### 6.4 三层文件安全（纵深防御）

| 层 | 代码 | 防什么 |
|---|---|---|
| Agent 工具层 | `read_file_tool` resolve 后必须在项目根内 | Agent 读系统文件 |
| Agent 工具层 | `terminal_tool` 黑名单 + cwd 锁定 | Agent 删库跑路 |
| HTTP 层 | `files.py` 目录白名单 + 路径穿越检查 | 前端请求读写任意文件 |

### 6.5 MD5 变更检测 + 索引持久化

`retrieve()` 前先比 MD5：没变 → 直接 `_load_index()` 复用磁盘索引；变了 → 重建并落盘。Embedding 调用只发生在内容真正变化时——**省 API 费用，也省冷启动时间**。

### 6.6 单例三件套

`agent_manager` / `session_manager` / `get_memory_indexer()` 都是模块级单例——FastAPI 多 worker 时注意：状态在进程内存 + 文件系统，多进程会各自持有一份（本项目本地单进程场景无碍）。

---

## 7. 配置系统详解

### 7.1 环境变量（backend/.env，模板见 .env.example）

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ | — | 对话模型密钥 |
| `DEEPSEEK_MODEL` | — | `deepseek-chat` | 模型名 |
| `DEEPSEEK_BASE_URL` | — | `https://api.deepseek.com` | 可换任意 OpenAI 兼容网关 |
| `OPENAI_API_KEY` | RAG 模式 | — | Embedding 向量化密钥 |
| `OPENAI_BASE_URL` | — | `https://ai.devtool.tech/proxy/v1` | Embedding 服务地址（内置代理默认值） |
| `EMBEDDING_MODEL` | — | `text-embedding-3-small` | 向量模型 |

> 💡 最低可用配置只需一个 `DEEPSEEK_API_KEY`——RAG 检索可后置开启。

### 7.2 运行时配置（backend/config.json）

| 键 | 类型 | 默认 | 读写方 |
|---|---|---|---|
| `rag_mode` | bool | `false` | `config_api.py` 持久化，`agent.py astream` 每轮读取 |

### 7.3 硬编码常量速查

| 常量 | 值 | 位置 | 作用 |
|---|---|---|---|
| `MAX_COMPONENT_LENGTH` | 20000 | prompt_builder.py | 单提示词组件截断线 |
| chunk_size / overlap | 256 / 32 | memory_indexer.py | 记忆分块参数 |
| 终端超时 / 截断 | 30s / 5000 | terminal_tool.py | 防命令挂死、防输出爆 Token |
| fetch 超时 / 截断 | 15s / 5000 | fetch_url_tool.py | 同上 |
| read_file 截断 | 10000 | read_file_tool.py | 同上 |
| 压缩阈值 / 比例 | 4 条 / 50% | compress.py | 最少消息数 / 压缩比例 |
| 标题长度 | ≤10 字（截 20） | chat.py / sessions.py | 自动起名约束 |
| 三栏最小宽度 | 200/280/360 | page.tsx | 拖拽下限 |

---

## 8. 如何运行和测试

### 8.1 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.10+ | 后端（PRD 强制 Type Hinting） |
| Node.js | 18+ | 前端 |
| DeepSeek API Key | — | 对话必需 |
| OpenAI 兼容 Key | 可选 | 仅 RAG 模式需要 |

### 8.2 启动后端（端口 8002）

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows；macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 创建 .env（参考 .env.example），至少填 DEEPSEEK_API_KEY=sk-xxx

uvicorn app:app --host 0.0.0.0 --port 8002 --reload
```

看到 `✅ mini OpenClaw backend ready` 即成功。

### 8.3 启动前端（端口 3000）

```bash
cd frontend
npm install
npm run dev
# 打开 http://localhost:3000
```

### 8.4 验证核心链路

1. 打开 `http://localhost:3000`，输入「查询北京天气」；
2. 观察思考链出现 `read_file`（读 get_weather 技能）→ `fetch_url` / `python_repl`；
3. `new_response` 生效：工具结束后**新气泡**开始打字机输出；
4. 首轮结束后侧边栏会话名自动变为 AI 生成的标题；
5. 点开侧边栏 Raw Messages：能看到完整 System Prompt（六文件拼接产物）与全部消息；
6. 右侧 Inspector 打开 `memory/MEMORY.md`，Ctrl+S 保存后 RAG 索引自动重建（后端日志出现 `🔄 Memory index rebuilt`）。

---

## 9. 复刻建议与学习路线

### 9.1 阶段路线（以快速上手 3 文件为起点）

| 阶段 | 做什么 | 耗时 |
|---|---|---|
| 1. 前端骨架 | 读 `ChatMessage.tsx` → `store.tsx` → `api.ts`，理解 SSE 事件驱动的 UI 状态机 | 0.5 天 |
| 2. 最小后端 | FastAPI + 一个假 echo 路由，跑通 POST SSE 解析（去掉 Agent） | 0.5 天 |
| 3. 接入真 Agent | `create_agent` + DeepSeek + 2 个工具（terminal/read_file），打通 `api/chat.py` 分段保存 | 1-2 天 |
| 4. 记忆与人格 | 六文件 prompt 拼接 + sessions 持久化 + load 合并 | 1 天 |
| 5. RAG 与压缩 | memory_indexer（MD5 检测）+ compress 链路 + Token 统计 | 1-2 天 |
| 6. 技能系统 | skills_scanner + SKILL.md 编写 + AGENTS.md 技能调用协议 | 0.5 天 |

### 9.2 学习资源

| 技术 | 资源 |
|---|---|
| LangChain create_agent | LangChain 1.x 官方文档（Agents 章节） |
| FastAPI SSE | sse-starlette + MDN Server-Sent Events |
| LlamaIndex | 官方 Starter（VectorStoreIndex / SentenceSplitter） |
| tiktoken | OpenAI Cookbook（token 统计） |
| Monaco | @monaco-editor/react 文档 |

### 9.3 二次开发切入点

- **加工具**：`tools/` 下新建 `xxx_tool.py` 写 `create_xxx_tool()`，到 `tools/__init__.py get_all_tools` 注册一行——完成；
- **加技能**：`skills/my_skill/` 放 SKILL.md（frontmatter 写清 name/description），重启后端即被扫描；
- **换模型**：改 `.env` 的 `DEEPSEEK_BASE_URL/MODEL` 指向任意 OpenAI 兼容服务；
- **补 BM25 混合检索**：`search_knowledge_tool.py` 换 LlamaIndex retriever 组合（见第 10 章 Q4）。

---

## 10. 常见问题

### Q1: 为什么一轮对话会产生多个 assistant 气泡？

后端把"每段工具调用之后的回答"存成独立 assistant 消息（Segment 机制，见 §6.1），前端收到 `new_response` 事件就新开气泡。这让"工具→回答→再工具→再回答"的节奏可视化。喂给 LLM 时又会自动合并，两不耽误。

### Q2: 为什么不用数据库？

PRD 明确定位"本地优先 + 文件即记忆"：JSON/Markdown 人类可读可编辑，数据主权完全在用户手里。代价是并发写有覆盖风险（单用户场景可接受）。

### Q3: RAG 模式和默认模式到底差在哪？

默认模式把 MEMORY.md **全文**拼进 System Prompt（记忆多时 Token 爆炸）；RAG 模式只在用户提问相关时**检索 top-3 片段**注入。切换开关持久化在 `config.json`，每轮对话实时生效（`astream` 每次现读）。

### Q4: 知识库检索是"混合检索"吗？

**不是**。PRD 要求 BM25+向量混合，当前 `search_knowledge_tool.py` 实现是 `as_query_engine(similarity_top_k=3)`——向量检索 + LLM 合成回答。工具 description 里的 "hybrid retrieval" 是超前描述，已在源码注释 `[注意]` 标注。补法：LlamaIndex `QueryFusionRetriever` 组合 BM25Retriever + VectorIndexRetriever。

### Q5: 为什么改了 workspace/*.md 立即生效，不用重启？

`_build_agent()` **每轮请求都重建** agent 并重读全部 prompt 文件（§6.3）。同理 MEMORY.md 保存后 `files.py` 会触发索引重建。

### Q6: 会话 JSON 里的 v1/v2 格式是什么？

v1 是裸消息数组（早期版本），v2 是带 title/时间戳/compressed_context 的对象。`_read_file` 读到 v1 自动迁移为 v2 并在下次写盘时落新格式——平滑升级零手工。

### Q7: 前端如何知道"后端在哪个端口"？

`api.ts` 的 `API_BASE` 用 `window.location.hostname:8002`——前端页面从哪个主机打开，就请求同主机的 8002。局域网另一台设备访问前端也能直连同主机的后端。

### Q8: 自动生成的标题不理想怎么办？

标题是 DeepSeek 用首轮对话前 200 字生成的 ≤10 字短句（temperature=0.3 求稳）。不满意可直接在侧边栏会话项上重命名（`PUT /sessions/{id}`），或点"手动生成"走 `generate-title` 端点（带前 10 字兜底）。

### Q9: Windows 下中文乱码/编码问题？

所有文件读写都显式 `encoding="utf-8"`；终端工具的 subprocess 也指定 `encoding="utf-8", errors="replace"`。若仍乱码，检查终端自身代码页（`chcp 65001`）。

### Q10: 多个浏览器同时用会怎样？

每个页面独立 session_id 时互不干扰；但**同一个 session 并发写**会互相覆盖（读-改-写无锁）。单用户本地场景无碍，多人使用需引入文件锁或迁移数据库。

---

## 附录：关键术语对照表

| 英文 | 中文 | 说明 |
|---|---|---|
| Agent | 智能体 | LLM + 工具的决策循环 |
| create_agent | （LangChain 1.x 标准 API） | 本项目构建 Agent 的唯一入口 |
| SSE | 服务端推送事件 | POST 长连接逐事件推送 |
| Segment | 分段 | 工具回合之间的回答切片 |
| System Prompt | 系统提示词 | 六文件拼接的人格+记忆载体 |
| Instruction-following | 指令遵循 | 技能范式：读说明书学能力 |
| Frontmatter | 文件头元数据 | SKILL.md 顶部的 YAML 块 |
| SKILLS_SNAPSHOT | 技能快照 | 启动时生成的技能 XML 清单 |
| RAG | 检索增强生成 | 按问题检索记忆片段注入上下文 |
| MEMORY.md | 长期记忆文件 | 跨会话人格记忆载体 |
| compressed_context | 压缩上下文 | 会话历史压缩后的累积摘要 |
| Embedding | 向量化 | 文本→语义向量 |
| SentenceSplitter | 句子分割器 | LlamaIndex 分块策略（256/32） |
| tiktoken / cl100k_base | Token 计数器/编码 | OpenAI 系 Token 估算 |
| Monaco Editor | 代码编辑器 | 前端文件检查器内核 |
| Raw Messages | 原始消息面板 | 查看 System Prompt 与完整消息的工具 |
| Klein Blue | 克莱因蓝 | 前端主色 #002fa7 |
