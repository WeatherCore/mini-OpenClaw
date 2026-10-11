# ============================================================
# compress.py — 对话历史压缩（长会话不爆 Token 的关键）。
# 职责：POST /api/sessions/{id}/compress，把会话前 50% 消息让 DeepSeek 压成 ≤500 字摘要，
#       原 50% 归档到 sessions/archive/，主文件留后半段 + compressed_context 累积摘要。
# 触发：前端 Sidebar.tsx 点 🔧 按钮弹确认框 → compressCurrentSession() 调此端点。
# 设计取舍：① 至少 4 条消息才让压（太少没意义）② 取 max(4, len//2) 条压（至少 4 条，保证摘要有料）
#           ③ temperature=0.3 求稳（摘要要忠实不该发挥）④ 失败抛 500 让前端提示。
# 压缩后链路：下次对话 load_session_for_agent 会把 compressed_context 以 [摘要] 形式注入头部。
# ============================================================
"""POST /api/sessions/{session_id}/compress — Compress conversation history."""

import os                                            # 读环境变量配 DeepSeek
import traceback                                      # 打印完整堆栈便于排错
from typing import Any                                 # 类型注解
from fastapi import APIRouter, HTTPException            # 路由 + 异常
from langchain_core.messages import HumanMessage        # DeepSeek 的入参消息类型
from graph.session_manager import session_manager      # 会话读写与压缩落盘

# 创建路由对象，用来注册接口
router = APIRouter()

# ────────────────────────────────────────────────────────────────────────────
# 压缩链路全景：
#
#   前端 Sidebar 点 🔧 → 确认框 → POST /sessions/{id}/compress
#     → load_session 取全部消息
#     → < 4 条？→ 400 拒绝（太少没压的意义）
#     → 取前 max(4, len//2) 条 = 待压缩段
#     → _generate_summary：格式化消息（每条 content 截 500 字）→ DeepSeek 生成 ≤500 字摘要
#     → session_manager.compress_history 落盘：
#         原 50% 归档到 sessions/archive/{id}_{ts}.json
#         主文件留后半段 + compressed_context 追加摘要（多次压缩用 --- 分隔）
#     → 返回 {archived_count, remaining_count}，前端刷新聊天区与 Token 统计
#
# 压缩后的记忆闭环：
#   下次对话 load_session_for_agent 会把 compressed_context 以
#   "[以下是之前对话的摘要]" 的 assistant 消息注入头部——长会话不爆 Token、记忆不丢。
#
# 摘要质量保障：
#   - temperature=0.3 求稳（摘要要忠实记录关键信息/决策/结论，不该发挥）
#   - 提示词要求"只输出摘要内容，不要添加额外说明"——防 LLM 加废话占 Token
#   - 每条消息 content 截前 500 字——防一条超长消息撑爆摘要输入
# ────────────────────────────────────────────────────────────────────────────

# _generate_summary（生成摘要）：把一批消息格式化后让 DeepSeek 压成 ≤500 字中文摘要
async def _generate_summary(messages: list[dict[str, Any]]) -> str:
    """Use DeepSeek to generate a compressed summary of messages."""

    # 延迟 import，避免模块加载期依赖
    from langchain_deepseek import ChatDeepSeek  

    # 新建 DeepSeek LLM 实例（与 agent.py 同款，但 temperature 更低求稳），专门做摘要生成
    llm = ChatDeepSeek(  
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        api_base=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        temperature=0.3,  # 低温求稳：摘要要忠实记录，不该发挥
    )

    # 把消息格式化成 "role: content" 行——每条 content 截前 500 字（防一条超长撑爆摘要输入）
    formatted = []  # 收集格式化后的消息行

    # 遍历待压缩消息，拼接成文本，每条消息最多截取500字符，防止超长
    for msg in messages:

        # 角色（user/assistant/system）
        role = msg.get("role", "unknown")
        # 消息内容 
        content = msg.get("content", "")  
        if content:  # 空内容跳过
            formatted.append(f"{role}: {content[:500]}")  # 截前 500 字防超长

    # 全部消息拼接成一整段对话文本
    conversation_text = "\n".join(formatted)

    # 摘要提示词：要求保留关键信息/决策/结论，≤500 字，只输出摘要不废话
    prompt = (
        "请将以下对话历史压缩为简洁的中文摘要，保留关键信息、决策和结论。"
        "摘要不超过500字。只输出摘要内容，不要添加额外说明。\n\n"
        f"{conversation_text}"  # 拼接待压缩的对话文本
    )

    # 调 DeepSeek 生成摘要
    result = await llm.ainvoke([HumanMessage(content=prompt)])  

    # 返回去首尾空白的摘要文本
    return result.content.strip()  


# compress_session（压缩会话）：POST /api/sessions/{session_id}/compress。
# 门槛：≥4 条消息才压。取前 50%（至少 4 条）压成摘要，落盘 + 返回归档/剩余条数。
@router.post("/sessions/{session_id}/compress")
async def compress_session(session_id: str) -> dict[str, Any]:
    """Compress the first 50% of conversation history into a summary."""

    # 读取这个会话全部消息
    messages = session_manager.load_session(session_id)  # 读会话全部消息

    # 消息数量小于4条，不允许压缩，抛出400错误
    if len(messages) < 4:
        # 400 拒绝请求，返回错误信息
        raise HTTPException(  
            status_code=400,
            detail="Not enough messages to compress (need at least 4)",
        )

    # 计算要压缩删除的消息条数：总消息数//2，最少压缩4条
    num_to_remove = max(4, len(messages) // 2)
    # 取前半部分对应条数的消息，用来生成摘要
    messages_to_compress = messages[:num_to_remove]

    try:  
        # 调用LLM生成摘要
        summary = await _generate_summary(messages_to_compress)

        # 调用session_manager的压缩方法：删掉前面num_to_remove条原始消息，替换成一条摘要消息
        session_manager.compress_history(session_id, summary, num_to_remove)  

        # 计算压缩之后剩下多少条消息
        remaining = len(messages) - num_to_remove 

        # 返回结果给前端
        return {
            "archived_count": num_to_remove,  # 归档压缩了多少条原始消息
            "remaining_count": remaining,     # 压缩后剩余消息数量
        }

    # 摘要生成或落盘失败
    except Exception as e:  
        traceback.print_exc()  # 打印完整堆栈便于排错
        # 500 让前端提示
        raise HTTPException(status_code=500, detail=f"Compression failed: {str(e)}")  