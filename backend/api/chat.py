# ============================================================
# chat.py — SSE 流式对话路由（后端核心，七类事件的生产者）。
# 职责：POST /api/chat，把 AgentManager 的双流事件翻译成 SSE 事件推给前端，并在 done 时按分段落盘。
# 三大机制在此交汇：
#   ① 双流解析：agent_manager.astream 产出 {type, ...} 事件流，本函数分发成 SSE；
#   ② 分段保存：每轮工具调用后的回答 = 一个 segment，done 时各存一条 assistant 消息；
#   ③ 自动起名：首轮对话结束调 sessions.py 的 generate_title，生成 ≤10 字标题推 title 事件。
#
# SSE 事件清单（前端 store.tsx 逐类处理）：
#   retrieval    — RAG 检索到记忆片段（紫色 RetrievalCard）
#   token        — LLM 逐字输出（追加到当前气泡，打字机效果）
#   new_response — 一轮工具结束、新回答开始（前端新开 assistant 气泡）
#   tool_start   — 工具调用开始（思考链追加卡片，转圈）
#   tool_end     — 工具调用结束（思考链卡片打勾，填 output）
#   done         — 本轮全部完成（含完整回答 + session_id）
#   title        — 首轮自动生成的标题（更新侧边栏会话名）
#   error        — 任何异常（气泡内追加错误提示，不打断连接）
# ============================================================
"""POST /api/chat — SSE streaming chat with Agent."""

import json                                          # SSE data 序列化
import traceback                                      # 打印完整堆栈便于排错
from typing import AsyncGenerator                     # 异步生成器类型注解
from fastapi import APIRouter                          # 路由
from pydantic import BaseModel                          # 请求体 schema
from sse_starlette.sse import EventSourceResponse      # SSE 响应封装（自动加 event:/data: 前缀）
from graph.agent import agent_manager                  # Agent 编排（双流事件生产者）
from graph.session_manager import session_manager      # 会话持久化（分段保存/合并/起名）
from api.sessions import generate_title                # AI 起名（复用 sessions.py 端点，消除 DRY 债务）

# 创建路由对象，用来注册接口
router = APIRouter()

# ChatRequest（对话请求体）：校验前端POST请求发过来的json参数，约束前端传 message + session_id + stream。
class ChatRequest(BaseModel):
    message: str                  # 用户输入的消息
    session_id: str = "default"   # 会话 ID，不传默认default（首次进入用）
    stream: bool = True           # True=SSE 流式，False=一次性返回（ainvoke）

# event_generator（SSE 事件生成器）：本文件的核心。
# 职责：监听 agent_manager.astream 输出的事件流，分发成 SSE 事件，并在 done 时按分段推送给前端
#
# 分段机制（Segment Pattern，见 ZHIDAO §6.1）示意：
#   token 流 ─→ 当前段 content 累积
#   new_response ─→ 封存当前段 → segments[]，开启新段
#   done ─→ 封存最后一段 → 批量存盘：
#              save_message(user) 一次
#              for seg in segments: save_message(assistant, seg.content, tool_calls=seg.tool_calls)
#              （tool_calls 含 id/name/args/output，标准协议字段齐全）
#
# 存与用：
#   存：按分段拆开（每轮工具回合 = 一条 assistant 消息，前端据此多气泡渲染）
#   用：load_session_for_agent 原样返回（带 tool_calls），_build_messages 拆成标准协议
#       AIMessage(tool_calls=[name/args/id]) + 独立 ToolMessage(output) 喂 LLM——跨轮工具不失忆。
async def event_generator(message: str, session_id: str) -> AsyncGenerator[dict, None]:
    """Generate SSE events from agent stream.

    Tracks multiple response segments — each time the agent finishes
    tool calls and starts generating new text, a new_response event is
    emitted and a new segment begins. Each segment is saved as a
    separate assistant message in the session history.
    """

    try:
        # 用合并后的历史喂 Agent（连续 assistant 已合并回 user/assistant 交替规范）
        history = session_manager.load_session_for_agent(session_id)

        # 判断是不是会话里第一条消息（用来判断是否需要自动生成会话标题）
        # 判断长度等于 0 → 历史消息是空列表 → 没有历史消息 → 接下来将会是是第一条消息
        is_first_message = len(history) == 0

        # segments：存放所有完整的回答片段（一轮对话可能多段，多次工具调用就会产生多段）
        # 例如：用户提问 → 调用工具 1 → AI 输出一段文字 → 再调用工具 2 → AI 再输出一段文字。这里每一段独立回答，就叫segment
        segments: list[dict] = []
        # current_segment：当前正在生成的回答片段，保存当前文本+本轮所有工具调用记录
        current_segment: dict = {"content": "", "tool_calls": []}

        # ── 事件分发循环 ──
        # 【核心】订阅agent_manager的流式事件，循环接收agent输出的自定义事件
        # 
        # agent_manager.astream 产出 {type, ...} 事件流，本循环按 type 分发到对应 SSE 事件。
        # 七类事件各自的归属：
        #   retrieval/new_response → 仅转发前端（不进 segment，不进历史）
        #   token → 累积进 current_segment.content + 转发前端（打字机）
        #   tool_start/tool_end → 累积进 current_segment.tool_calls + 转发前端（思考链）
        #   done → 封存最后段 + 批量落盘 + 转发前端 + 首轮起名
        async for event in agent_manager.astream(message, history):  # 消费 Agent 双流事件

            # 获取事件类型，未知类型兜底为unknown
            event_type = event.get("type", "unknown")

            # ========== 事件1：retrieval 记忆检索完成（RAG） ==========
            if event_type == "retrieval":
                # retrieval 事件只转发前端（不进 segment），它由 agent.py 在 RAG 模式下检索记忆后产生，
                # 前端据此渲染紫色 RetrievalCard 让用户看到 Agent 检索了哪些记忆。

                # 包装成SSE规范事件，event字段是事件名，data是json字符串
                yield {
                    "event": "retrieval",
                    "data": json.dumps( 
                        {"query": event["query"], "results": event["results"]},
                        ensure_ascii=False, 
                    ),
                }

            # ========== 事件2：token，LLM输出文字片段（打字机） ==========
            elif event_type == "token":
                # token 事件：累积进当前段（用于最终落盘）+ 转发前端（打字机效果）。
                # 前端 store.tsx 收到后追加到 currentAssistantIdRef 指向的气泡 content。

                # 把token追加到当前片段的content里，累积完整文本
                current_segment["content"] += event["content"]

                # 推 token 事件给前端追加打字机效果
                yield {  
                    "event": "token",
                    "data": json.dumps({"content": event["content"]}, ensure_ascii=False),  # 序列化单字内容
                }

            # ========== 事件3：new_response 新一段回答开始（工具跑完之后） ==========
            # 刚刚跑完工具，LLM 准备开始输出新一轮文字回答，开启一段全新的 AI 回答片段（segment）
            elif event_type == "new_response":

                # 把【上一段已经结束的回答片段】存入segments数组保存起来
                segments.append(current_segment)
                # 新建一个空的片段对象，准备接收接下来新的token和工具记录
                current_segment = {"content": "", "tool_calls": []} 

                # 向前端推送new_response事件，前端收到可以渲染分割线，区分前后两段回答
                yield {
                    "event": "new_response",
                    "data": json.dumps({}, ensure_ascii=False),
                }

            # ========== 事件4：tool_start 准备调用工具 ==========
            elif event_type == "tool_start":

                # 把这个工具调用记录添加到当前片段的 tool_calls 数组
                current_segment["tool_calls"].append({
                    "id": event.get("id", ""),        # 工具调用 id（标准协议关联 assistant↔tool 消息）
                    "tool": event["tool"],
                    "input": event.get("input", ""),  # 截断 str（前端思考链展示用）
                    "args": event.get("args", {}),    # 原始 dict（喂 LLM 标准协议用）
                })

                # 推 tool_start 事件给思考链追加卡片（转圈）
                yield {
                    "event": "tool_start",
                    "data": json.dumps(  # 序列化工具开始事件（含 id，前端可选用精确配对）
                        {"tool": event["tool"], "id": event.get("id", ""), "input": event["input"]},
                        ensure_ascii=False,
                    ),
                }

            # ========== 事件5：tool_end 工具执行完毕，拿到输出 ==========
            elif event_type == "tool_end":
                # 配对 input ↔ output：优先按 tool_call_id 精确匹配（标准协议），
                # id 缺失或没配上时回退到逆序同名匹配（兼容旧会话/异常情况）。
                tc_id = event.get("tool_call_id", "")
                matched = False
                if tc_id:  # 有 id 先按 id 配
                    for tc in reversed(current_segment["tool_calls"]):
                        if tc.get("id") == tc_id and "output" not in tc:
                            tc["output"] = event["output"]
                            matched = True
                            break
                if not matched:  # id 没配上 → 逆序同名兜底
                    for tc in reversed(current_segment["tool_calls"]):
                        if tc["tool"] == event["tool"] and "output" not in tc:
                            tc["output"] = event["output"]
                            break

                # 推 tool_end 事件给思考链卡片打勾
                yield {  
                    "event": "tool_end",
                    "data": json.dumps(  # 序列化工具结束事件
                        {"tool": event["tool"], "output": event["output"]},
                        ensure_ascii=False,
                    ),
                }

            # ========== 事件6：done，Agent一轮完整对话全部结束 ==========
            elif event_type == "done":  # 本轮全部完成
                # ── done 落盘流程 ──
                # 1. 封存最后一段进 segments
                # 2. 存用户消息一次（流式中途失败也不会留半截用户消息，因为存盘在 done 才发生）
                # 3. 逐段存 assistant 消息（每段带各自的 tool_calls）
                # 4. 推 done 事件给前端结束流式
                # 5. 首轮对话调 generate_title 起名 + 推 title 事件

                # 把最后还没存的current_segment加入segments
                segments.append(current_segment)

                # 保存用户输入消息到会话 json，
                # 只存一次（在 done 时，避免流式中途失败留下半截用户消息）
                session_manager.save_message(session_id, "user", message)

                # 遍历所有回答片段，逐个保存assistant消息，附带tool_calls！
                for seg in segments:
                    # 如果这个片段有tool_calls就赋值，没有就传None
                    tc = seg["tool_calls"] if seg["tool_calls"] else None  # 有工具调用才带 tool_calls

                    # 保存AI输出回答到会话 json
                    session_manager.save_message(  # 存分段 assistant 消息
                        session_id, "assistant", seg["content"], tool_calls=tc
                    )

                # 推 done 事件（含完整回答 + session_id，前端结束流式状态）
                yield {  
                    "event": "done",
                    "data": json.dumps(  # 序列化完成事件
                        {"content": event["content"], "session_id": session_id},
                        ensure_ascii=False,
                    ),
                }

                # 如果是会话第一条消息，自动生成会话标题，推title事件给前端
                if is_first_message:  # 首轮
                    result = await generate_title(session_id)  # 调 sessions.py 端点（返回 dict）
                    if result and result.get("title"):  # 有标题
                        yield {  # 推 title 事件，前端更新侧边栏会话名
                            "event": "title",
                            "data": json.dumps(  # 序列化标题事件
                                {"session_id": session_id, "title": result["title"]},
                                ensure_ascii=False,
                            ),
                        }

    # 全局捕获所有异常，发生错误向前端推送error事件，不会直接崩溃服务，不打断 SSE 连接
    except Exception as e:
        traceback.print_exc()  # 打印堆栈便于排错

        # 推 error 事件给前端气泡内追加错误提示
        yield {  
            "event": "error",
            "data": json.dumps({"error": str(e)}, ensure_ascii=False),  # 序列化错误信息
        }


# chat（对话入口）：POST /api/chat。stream=true 走 SSE，否则 ainvoke 一次性返回。
#
# 为什么分两路：
#   - stream=true（默认）：EventSourceResponse 包 event_generator，逐事件推前端，体验好（打字机+思考链实时）
#   - stream=false：ainvoke 一次性返回完整回答，无流式体验，但实现简单——留作 API 调试/脚本调用
# 前端 store.tsx 永远走 stream=true；stream=false 基本没人用，但留着不删以备调试。
@router.post("/chat")
async def chat(request: ChatRequest):

    # 判断前端传的stream参数，是否开启流式输出
    if request.stream:
        # 流式模式：返回SSE长连接响应
        # EventSourceResponse 自动加 event:/data: 前缀 + 维持连接的心跳，前端 streamChat 解析
        #   把 event_generator 这个异步生成器包装成SSE协议，持续向后端推送事件
        return EventSourceResponse(
            event_generator(request.message, request.session_id)  # 事件生成器
        )
    
    # 非流式兜底：一次性调用并返回完整回答（前端基本不用，留作 API 调试）
    # ainvoke 内部走 create_agent + ainvoke，从结果里倒序找首条 AI 消息返回。
    result = await agent_manager.ainvoke(request.message, request.session_id)
    # 返回json，key是reply，值是AI完整回答文本
    return {"reply": result}