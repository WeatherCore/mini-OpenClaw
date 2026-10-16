# ============================================================
# tokens.py — Token 记账（会话级 + 文件级）：统计会话、文件的token数量，用来预估大模型输入开销
# 职责：用 tiktoken cl100k_base 给会话（System Prompt + 全部消息）与文件列表算 Token 数，供前端侧边栏显示。
# 谁在用：Sidebar.tsx 展开 Raw Messages 时拉会话 Token；InspectorPanel.tsx 展开 Memory Tab 时批量拉文件 Token。
# 设计取舍：encoder 实例模块级缓存（tiktoken.get_encoding 费时，只跑一次）；会话统计算全量——
#           System Prompt + 每条消息 content + tool_calls（函数名/参数/输出）+ compressed_context 压缩摘要。
# [注意] cl100k_base 是 GPT-3.5/GPT-4 系编码，对 DeepSeek 估算略有偏差，但量级够用。
# ============================================================
"""GET/POST /api/tokens — Token counting for sessions and files."""

from pathlib import Path                               # 路径拼接 + 越界检查
from typing import Any                                 # dict[str, Any] 类型注解
import tiktoken                                        # OpenAI 的 Token 计数库
import json                                            # tool_calls 序列化为文本再编码算 Token
from fastapi import APIRouter, HTTPException            # 路由 + 异常
from pydantic import BaseModel                          # 请求体 schema
from graph.session_manager import session_manager      # 读会话消息 + 取压缩摘要
from graph.prompt_builder import build_system_prompt    # 拼 System Prompt 算它的 Token

# 创建路由对象，用来注册接口
router = APIRouter()

# BASE_DIR = backend/：文件 Token 统计的沙箱锚点（防前端请求读项目外文件）
BASE_DIR = Path(__file__).resolve().parent.parent  # tokens.py 的父父目录 = backend/

# 加载cl100k_base编码器，gpt3.5/gpt4、deepseek很多模型都兼容这个编码
_encoder = tiktoken.get_encoding("cl100k_base")


# ────────────────────────────────────────────────────────────────────────────
# 两个端点的数据形态：
#
# 1) GET /api/tokens/session/{session_id}
#    返回 {"system_tokens": N, "message_tokens": M, "compressed_tokens": K, "total_tokens": N+M+K}
#    前端 Sidebar.tsx 展开 Raw Messages 时拉一次，显示 "msg+comp+sys=total tokens"。
#
# 2) POST /api/tokens/files  body: {"paths": ["memory/MEMORY.md", ...]}
#    返回 {"files": [{"path": "memory/MEMORY.md", "tokens": 123}, ...]}
#    前端 InspectorPanel.tsx Memory Tab 展开时批量拉，文件列表每项显示 Token 徽标。
#
# 计数口径（全量）：
#   - 会话：System Prompt（六文件拼接产物）+ 每条消息 content + tool_calls（name/args/output 整体编码）
#     + compressed_context 压缩摘要（被压缩过的长会话才有，连注入前缀一起算）。
#   - 文件：整文件 content 一次性编码计数。
#
# 越界保护：文件路径 resolve 后必须仍在 BASE_DIR 内，越界/不存在/读异常都返回 tokens:0，
#           不抛异常——前端只显示 0，不打断批量统计。
# ────────────────────────────────────────────────────────────────────────────


# _count_tokens（算 Token）：用缓存的 encoder 把文本编码成 token id 列表，长度即 Token 数。
def _count_tokens(text: str) -> int:
    """Count tokens using cl100k_base encoding."""

    # encode：把文本切分成token数组；len拿到token总数
    return len(_encoder.encode(text))


# get_session_token_count（会话 Token 统计）：GET /api/tokens/session/{session_id}。
# 统计一个会话全部 token：System Prompt + 每条消息（content + tool_calls）+ 压缩摘要。前端侧边栏显示总 Token。
@router.get("/tokens/session/{session_id}")
async def get_session_token_count(session_id: str) -> dict[str, Any]:
    """Count tokens in a session: system prompt + messages (content + tool_calls) + compressed context."""

    # 构建完整系统提示词（技能、知识库等全部内容）
    system_prompt = build_system_prompt(BASE_DIR)
    # System Prompt 的 Token 数量
    system_tokens = _count_tokens(system_prompt)

    # 加载原始消息（带 tool_calls）。
    # 注意：不能用 load_session_for_agent——它合并连续 assistant 时只保留 role+content、丢掉 tool_calls
    # 故走 load_session 拿磁盘原始消息（tool_calls 字段原样在）。
    messages = session_manager.load_session(session_id)
    # 累加消息 Token（content + tool_calls）
    message_tokens = 0

    # 循环累加每条消息：① 正文 content ② 工具调用（函数名+参数+输出，整体 json.dumps 编码）
    for msg in messages:
        # ① 消息正文
        message_tokens += _count_tokens(msg.get("content", ""))
        # ② 工具调用：旧版只算 content 漏掉 tool_calls，编码 agent 工具回合多时能差几千 token
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            message_tokens += _count_tokens(json.dumps(tool_calls, ensure_ascii=False))

    # ③ 压缩摘要 compressed_context：长会话被 archive_and_compress 压缩后才有（普通会话为空）。
    # 喂 LLM 时包成 system 消息注入头部（见 load_session_for_agent），这里连前缀一起算贴近真实输入。
    compressed = session_manager.get_compressed_context(session_id) or ""
    compressed_tokens = _count_tokens(f"[以下是之前对话的摘要]\n{compressed}") if compressed else 0

    # 返回四段：系统/消息/压缩摘要/总计，前端 Sidebar 据此显示 "msg+comp+sys=total"
    return {
        "system_tokens": system_tokens,
        "message_tokens": message_tokens,
        "compressed_tokens": compressed_tokens,
        "total_tokens": system_tokens + message_tokens + compressed_tokens,  # 总 Token = 系统 + 消息 + 压缩摘要
    }


# FileTokenRequest（文件 Token 请求体）：前端传一批相对路径，批量算 Token。
class FileTokenRequest(BaseModel):
    paths: list[str]     # 相对项目根的文件路径列表（InspectorPanel 的 6 个 memory 文件）

# get_file_token_counts（文件 Token 统计）：POST /api/tokens/files {paths: [...]}。
# 批量算文件 Token，供 InspectorPanel 文件列表每项显示 Token 徽标。
# 安全：路径越界或不存在都返回 tokens:0，不抛异常（前端只显示 0 不报错）。
@router.post("/tokens/files")
async def get_file_token_counts(request: FileTokenRequest) -> dict[str, Any]:
    """Count tokens for a list of files."""

    # 收集每个文件的 {path, tokens}
    results: list[dict[str, Any]] = []

    # 遍历前端传过来的每一个文件相对路径
    for rel_path in request.paths:

        # 路径标准化：统一斜杠，去掉前面的 ./
        normalized = rel_path.replace("\\", "/").lstrip("./")
        # 拼绝对路径并 resolve
        full_path = (BASE_DIR / normalized).resolve()

        # 越界：跑出项目根
        if not str(full_path).startswith(str(BASE_DIR)):
            # 返回 0，前端显示 0
            results.append({"path": rel_path, "tokens": 0})
            # 跳过这个文件
            continue

        # 文件不存在 → token记0
        if not full_path.exists():
            results.append({"path": rel_path, "tokens": 0})  # 同样返回 0
            continue  # 跳过

        try:
            # 读文件 + 算 Token
            content = full_path.read_text(encoding="utf-8")
            tokens = _count_tokens(content)
            # 记录结果
            results.append({"path": rel_path, "tokens": tokens})

        # 读异常（权限/编码）：返回 0，不抛异常打断批量
        except Exception:
            results.append({"path": rel_path, "tokens": 0})

    # 返回全部文件的 Token 结果
    return {"files": results}
