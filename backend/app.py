# ============================================================
# app.py — mini OpenClaw 后端的"点火开关"与"路由总台"。
# 整个后端服务的入口：uvicorn app:app --port 8002 加载的本模块。
# 职责只有两件：
#   ① lifespan 启动钩子：服务起来时按"扫技能 → 装 Agent → 建记忆索引"三步初始化，
#      任一步失败服务起不来（print "✅ mini OpenClaw backend ready" 才放行请求）；
#   ② 装配 FastAPI app 实例：挂 CORS 中间件 + 注册 6 个 /api 前缀路由 + 暴露根健康检查。
# 设计取舍：lifespan 内的 import 全部延迟到函数体（见下注释），既避免循环依赖、又让冷启动更快。
# ============================================================
"""Mini-OpenClaw Backend — FastAPI Entry Point"""

import os                                              # 仅 os.environ 读取环境变量用（本文件未直接使用，留给子模块）
from contextlib import asynccontextmanager             # FastAPI 0.93+ 的 lifespan 协议装饰器，替代已废弃的 @app.on_event
from pathlib import Path                               # 跨平台路径拼接，BASE_DIR 的基石

from dotenv import load_dotenv                         # 从 backend/.env 把 DEEPSEEK_API_KEY 等注入 os.environ
from fastapi import FastAPI                            # 应用主体
from fastapi.middleware.cors import CORSMiddleware     # 跨域中间件：前端 3000 ↔ 后端 8002 必须放行

# 启动即加载 .env：必须在任何读 os.environ 的子模块（agent/memory_indexer）import 之前完成
load_dotenv()

# BASE_DIR = backend/ 绝对路径：所有"项目根"判定的锚点（文件白名单、技能扫描、会话目录都基于它）
BASE_DIR = Path(__file__).resolve().parent


# lifespan（启动生命周期）：FastAPI 推荐的启动/关闭钩子写法。yield 之前是启动逻辑，yield 之后是关闭清理。
# 这里只做启动（关闭无资源需释放）。三步顺序不可乱：技能快照要先于 Agent 装配（Agent 要看技能列表），记忆索引可最后。
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: scan skills, initialize agent, build memory index."""
    # 延迟 import：这三行放函数内而非文件顶，是有意为之——
    # ① 避免 app.py 在被 uvicorn 导入的瞬间就触发 skills_scanner/agent/memory_indexer 的副作用导入链；
    # ② 让 BASE_DIR、load_dotenv() 先就位，子模块拿到的环境变量与路径才是准备好的。
    from tools.skills_scanner import scan_skills       # 启动时扫描 skills/*/SKILL.md 生成技能快照
    from graph.agent import agent_manager              # 单例 Agent 管理器（create_agent + DeepSeek + 5 工具）
    from graph.memory_indexer import get_memory_indexer # 单例记忆向量索引器（MEMORY.md → LlamaIndex）

    # 第 1 步：扫描技能目录，生成 SKILLS_SNAPSHOT.md（Agent 看到的能力清单）；缺失会以空清单兜底
    scan_skills(BASE_DIR)
    # 第 2 步：装配 AgentManager——建 ChatDeepSeek 实例、组装 5 大工具、绑定 session_manager
    agent_manager.initialize(BASE_DIR)

    # 第 3 步：构建记忆向量索引供 RAG 模式使用（默认 rag_mode=False 时索引仍建好待命，开启即可检索）
    indexer = get_memory_indexer(BASE_DIR)
    indexer.rebuild_index()  # 落盘到 storage/memory_index/，MEMORY.md 内容没变则下次复用免重建

    # 启动完成的标志位：前端/运维看到这行日志就知道后端已就绪可接客
    print("✅ mini OpenClaw backend ready")
    # yield 把控制权交还给 FastAPI 事件循环，开始接请求；此处之后是关闭逻辑（本项目无）
    yield


# app 实例：title/version 走进 /openapi.json 与 Swagger 文档；lifespan 把启动钩子挂上去
app = FastAPI(title="mini OpenClaw", version="0.1.0", lifespan=lifespan)

# CORS 全放开（本地开发友好）：allow_origins=["*"] + allow_credentials=True 严格说违反 CORS 规范
# （浏览器会拒绝带凭证的通配源），但本项目前后端同机/局域网使用、无 Cookie 鉴权，实际无碍；
# 部署到公网时应收紧为 ["http://localhost:3000"] 等显式源。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # 允许任意来源：本地开发够用，生产应白名单化
    allow_credentials=True,  # 允许携带凭证（本项目暂未用 Cookie，预留）
    allow_methods=["*"],      # 放行所有 HTTP 方法
    allow_headers=["*"],      # 放行所有请求头
)

# 路由注册：6 个 APIRouter 各自从 api/<name>.py 引入，统一挂到 /api 前缀下。
# import 放在 app 实例化之后，是为了让 lifespan 先绑定——避免路由模块导入时触发循环依赖。
from api.chat import router as chat_router             # POST /api/chat SSE 流式对话（核心）
from api.files import router as files_router           # GET/POST /api/files 文件读写 + 技能列表
from api.sessions import router as sessions_router     # 会话 CRUD + 原始消息 + AI 起名
from api.tokens import router as tokens_router         # Token 统计（tiktoken cl100k_base）
from api.compress import router as compress_router     # 对话历史压缩
from api.config_api import router as config_router     # RAG 模式开关

# 逐个挂载：prefix="/api" 让路由内定义的 "/chat" 实际暴露成 "/api/chat"，前后端契约统一
app.include_router(chat_router, prefix="/api")      # 对话 SSE 流式（核心路由）
app.include_router(files_router, prefix="/api")     # 文件读写 + 技能列表
app.include_router(sessions_router, prefix="/api")  # 会话 CRUD + AI 起名
app.include_router(tokens_router, prefix="/api")    # Token 统计
app.include_router(compress_router, prefix="/api")  # 对话历史压缩
app.include_router(config_router, prefix="/api")    # RAG 模式开关


# root（健康检查）：给前端 ChatMessage.tsx 的 401 排障卡片"检查后端状态"链接指向这里（http://localhost:8002/），
# 返回最小状态 JSON，能通即说明后端活着。
@app.get("/")
async def root():
    return {"name": "mini OpenClaw", "version": "0.1.0", "status": "running"}


# ────────────────────────────────────────────────────────────────────────────
# 启动后的事件循环：uvicorn 把 app 实例跑起来 → lifespan 进 startup → 接请求。
# 6 个路由各自的实现细节见 api/ 与 graph/ 目录的逐文件注释；
# 前端通过 lib/api.ts 的 streamChat() 与本服务建立 POST SSE 长连接。
# ────────────────────────────────────────────────────────────────────────────
