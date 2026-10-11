# ============================================================
# sessions.py — 会话 CRUD + 原始消息 + AI 起名（7 个端点）。
# 职责：会话的增删改查 + Raw Messages（含 System Prompt）+ 历史 + AI 生成标题。
# 谁在用：前端 Sidebar.tsx（会话列表/重命名/删除/Raw Messages）、store.tsx（历史加载/标题更新）。
# 设计取舍：
#   ① 标题生成与 chat.py 的 _generate_title 逻辑重复（已知 DRY 债务），但本端点带降级兜底：
#      LLM 失败时用用户消息前 10 字当标题，保证总有标题不报错。
#   ② get_raw_messages 把 System Prompt 拼在消息头部——Raw Messages 面板能看到完整 LLM 入参。
#
# 端点一览：
#   GET    /sessions                       列表（mtime 倒序）
#   POST   /sessions                       新建（session-{uuid12}）
#   PUT    /sessions/{id}                  重命名
#   DELETE /sessions/{id}                  删除
#   GET    /sessions/{id}/messages         原始消息（含 System Prompt 头）
#   GET    /sessions/{id}/history          历史（含 tool_calls，无 System Prompt）
#   POST   /sessions/{id}/generate-title   AI 起名（带兜底）
# ============================================================
"""Session CRUD API — list / create / rename / delete / raw messages / generate title."""

import os                                            # 读环境变量配 DeepSeek
import uuid                                            # 生成 session_id
from pathlib import Path                               # BASE_DIR 沙箱锚点
from fastapi import APIRouter, HTTPException            # 路由 + 异常
from pydantic import BaseModel                          # 请求体 schema
from graph.session_manager import session_manager      # 会话读写实现
from graph.prompt_builder import build_system_prompt    # 拼 System Prompt（Raw Messages 头部）

# 创建路由对象，用来注册接口
router = APIRouter()

# BASE_DIR = backend/：build_system_prompt 的入参，决定从哪读六文件
BASE_DIR = Path(__file__).resolve().parent.parent  # sessions.py 父父目录 = backend/


# ── Request models ──────────────────────────────────────────

# RenameRequest（重命名请求体）：约束前端传 title 字段。
# title 是用户新输入的会话名，会覆盖 AI 自动生成的标题。
class RenameRequest(BaseModel):
    title: str  # 新标题

# ── Endpoints ───────────────────────────────────────────────
# 会话生命周期：
#   新建（POST /sessions）→ 拿 session-{uuid12} ID + 空会话元数据
#   对话（POST /chat 在 chat.py）→ 消息追加进 sessions/{id}.json
#   查看（GET /sessions/{id}/messages 或 /history）→ 读消息渲染
#   重命名（PUT /sessions/{id}）→ 改 title
#   删除（DELETE /sessions/{id}）→ 删 sessions/{id}.json
#   压缩（POST /sessions/{id}/compress 在 compress.py）→ 归档 + 摘要
#
# 会话 ID 格式：session-{uuid12}，uuid.uuid4().hex[:12] 取 12 位十六进制，碰撞概率极低。

# list_sessions（会话列表）：GET /api/sessions。获取所有会话，携带标题、更新时间等元数据，前端会话列表页面使用
@router.get("/sessions")
async def list_sessions():
    """List all sessions with title and metadata."""

    # 调用session_manager的list_sessions方法，读取sessions目录下所有会话json
    sessions = session_manager.list_sessions()
    # 返回列表给前端侧边栏
    return {"sessions": sessions}  


# create_session（新建会话）：POST /api/sessions。生成 session-{uuid12} ID，建空会话返回元数据。
# 前端点"New Chat"调此端点，拿新会话 ID 后切到新会话（setSessionId）。
@router.post("/sessions")
async def create_session():
    """Create a new empty session."""

    # uuid生成唯一id，session-后面截取12位十六进制字符
    session_id = f"session-{uuid.uuid4().hex[:12]}"
    # 调用session_manager创建会话文件，生成初始元信息
    meta = session_manager.create_session(session_id)

    # 返回 {id, title, created_at, updated_at} 给前端
    return meta  


# rename_session（重命名会话）：PUT /api/sessions/{id}，返回 {id, title, created_at, updated_at} 给前端
# 前端会话项"重命名"调此端点，AI 自动标题不满意时手动改。
@router.put("/sessions/{session_id}")
async def rename_session(session_id: str, req: RenameRequest):
    """Rename an existing session."""

    try:
        # 调用session_manager修改会话json里面的title字段
        session_manager.rename_session(session_id, req.title)  # 改 title 落盘
    except FileNotFoundError:
        # 如果会话文件不存在，抛出404给前端
        raise HTTPException(status_code=404, detail="Session not found")  # 404

    # 返回新标题
    return {"id": session_id, "title": req.title}  


# delete_session（删除会话）：DELETE /api/sessions/{id}。
# 前端会话项"删除"调此端点（带二次确认）。删 sessions/{id}.json，主文件即消失。
@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""

    session_manager.delete_session(session_id)  # 删 sessions/{id}.json

    # 返回删除确认
    return {"status": "deleted", "id": session_id}  


# get_raw_messages（原始消息）：GET /api/sessions/{id}/messages。
# 返回完整 LLM 入参：System Prompt 拼在消息头部 + 会话全部消息。
# Raw Messages 面板数据源——让用户审视 Agent 真正收到了什么。
#
# 与 history 的区别：
#   - messages：含 System Prompt（拼在头部），不含 tool_calls → 给 Raw Messages 面板审视 LLM 入参
#   - history：不含 System Prompt，含 tool_calls → 给前端历史加载渲染气泡
# 两个端点服务两个场景，数据形态不同是设计取舍而非冗余。
@router.get("/sessions/{session_id}/messages")
async def get_raw_messages(session_id: str):
    """Get complete raw messages including system prompt."""

    # 读会话原始数据（title + messages）
    data = session_manager.get_raw_messages(session_id)  

    # 拼当前 System Prompt（六文件产物）
    system_prompt = build_system_prompt(BASE_DIR)  

    # 把 System Prompt 作为第一条 system 消息拼在头部——这是 LLM 真正收到的完整消息序列
    all_messages = [{"role": "system", "content": system_prompt}] + data.get("messages", []) 

    # 返回完整原始消息
    return {
        "session_id": session_id, 
        "title": data.get("title", ""), 
        "messages": all_messages
    }  


# get_session_history（会话历史）：GET /api/sessions/{id}/history。
# 与 messages 的区别：不含 System Prompt，但含 tool_calls——给前端历史加载用。
# store.tsx setSessionId 加载历史时调此端点，把后端消息映射成前端气泡（含 toolCalls 渲染思考链）。
@router.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str):
    """Get conversation history for display (no system prompt, includes tool_calls)."""

    # 读会话消息（含 tool_calls）
    messages = session_manager.load_session(session_id)  
    # 返回历史给前端 store.tsx 加载
    return {"session_id": session_id, "messages": messages}  


# generate_title（AI 起名）：POST /api/sessions/{id}/generate-title。
# 取首轮 user + assistant 各 200 字让 DeepSeek 生成 ≤10 字标题；LLM 失败用用户消息前 10 字兜底。
#
# 兜底设计（为什么 try 整个 LLM 调用）：
#   起名是辅助功能，不该因为它失败就让前端拿到 500。所以 LLM 不通/超时/返回异常时，
#   用用户消息前 10 字当标题——保证总有标题、不报错。这是"辅助功能降级"的典型范例。
#
# 与 chat.py _generate_title 的关系（DRY 债务）：
#   chat.py 在首轮对话结束时也调一次起名（流式 title 事件），本端点是用户手动点"生成标题"
#   或重命名失败时的兜底入口。两处逻辑重复，但本端点多了降级兜底——后续应抽到公共函数。
@router.post("/sessions/{session_id}/generate-title")
async def generate_title(session_id: str):
    """Use DeepSeek to generate a short title from the first conversation turn."""

    # 读会话消息
    messages = session_manager.load_session(session_id)  

    # 没消息没法起名
    if not messages:  
        raise HTTPException(status_code=400, detail="No messages to generate title from")  # 400

    # 取首轮 user 消息 + 首轮 assistant 回复（各截前 200 字防超长）
    first_user = ""  # 首条用户消息
    first_assistant = ""  # 首条助手回复

    # 遍历找首轮
    for msg in messages:  
        if msg["role"] == "user" and not first_user:  # 找到首条 user
            first_user = msg["content"][:200]  # 截前 200 字
        elif msg["role"] == "assistant" and not first_assistant:  # 找到首条 assistant
            first_assistant = msg["content"][:200]  # 截前 200 字
        if first_user and first_assistant:  # 首轮都找到了
            break  # 跳出

    if not first_user:  # 没有 user 消息（异常情况）
        raise HTTPException(status_code=400, detail="No user message found")  # 400

    try:  # 调 DeepSeek 起名（失败走 except 兜底）
        from langchain_deepseek import ChatDeepSeek  # 延迟 import
        from langchain_core.messages import HumanMessage as HM  # 入参消息类型

        llm = ChatDeepSeek(  # 建 DeepSeek 实例（temperature=0.3 求稳）
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            api_base=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            temperature=0.3,  # 低温：标题要简洁准确不该发挥
        )

        prompt = (  # 起名提示词：≤10 字中文，不加引号标点
            f"根据以下对话内容，生成一个不超过10个字的中文标题，只输出标题文本，不要加引号或标点。\n\n"
            f"用户: {first_user}\n"
            f"助手: {first_assistant}"
        )

        result = await llm.ainvoke([HM(content=prompt)])  # 调 DeepSeek
        title = result.content.strip().strip('"\'""''')[:20]  # 去空白 + 去引号 + 截 20 字

        session_manager.update_title(session_id, title)  # 落盘标题
        return {"session_id": session_id, "title": title}  # 返回 AI 标题

    except Exception as e:  # LLM 调用失败：降级兜底，绝不让起名端点报错
        fallback_title = first_user[:10].strip()  # 用用户消息前 10 字兜底
        session_manager.update_title(session_id, fallback_title)  # 落盘兜底标题
        return {"session_id": session_id, "title": fallback_title}  # 返回兜底标题