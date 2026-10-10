# ============================================================
# agent.py — AgentManager（Agent 之心，整个 mini-OpenClaw 的顶层入口类，最核心的编排层）
# 职责：单例 agent_manager。持有一个 DeepSeek LLM + 5 个工具，把 LangChain 双流事件翻译成前端友好的 7 类事件。
# 三大机制在此交汇：
#   ① 双流事件解析：astream 用 stream_mode=["messages","updates"] 一次订阅同时拿字级 token 与节点级工具事件；
#   ② 每请求重建 Agent：_build_agent 每轮对话都重读 prompt 文件 + create_agent——改 SOUL/MEMORY 立即生效；
#   ③ 分段边界检测：tools_just_finished 标志位捕捉"工具跑完、新回答开始"的时刻，yield new_response 事件。
# 谁在用：api/chat.py event_generator 调 astream 拿事件流；app.py lifespan 调 initialize 装配。
# ============================================================
"""AgentManager — Core Agent using LangChain create_agent API with DeepSeek."""

import os                                            # 读环境变量配 DeepSeek
from pathlib import Path                               # base_dir 类型
from typing import Any, AsyncGenerator                 # 类型注解
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage  # LangChain 消息类型
from config import get_rag_mode                        # 读 RAG 开关（每轮现读）
from graph.prompt_builder import build_system_prompt    # 拼 System Prompt（六文件）
from graph.session_manager import session_manager      # 会话读写
from tools import get_all_tools                        # 5 大工具工厂

# AgentManager（Agent 管理器）：单例。装配 LLM + 工具，对外暴露 astream/ainvoke 两个入口。
class AgentManager:
    """Manages the Agent lifecycle: initialization, streaming, invocation."""

    # __init__（初始化）：三个字段先置空，等 initialize(base_dir) 时才填充。
    # 延迟填充是为了让模块级单例 agent_manager 可以在 import 时就创建（不依赖 base_dir），
    # 真正的装配推迟到 app.py lifespan 调 initialize 时——避免 import 副作用。
    def __init__(self) -> None:
        self._base_dir: Path | None = None  # 项目根目录
        self._tools: list = []              # 存放全部工具列表
        self._llm = None                    # LLM大模型实例

    # initialize（启动装配）：app.py lifespan 调一次。建 LLM + 组装工具 + 初始化 session_manager。
    # 调用顺序：base_dir → tools → LLM → session_manager（session_manager 要等 base_dir 就位）。
    #
    # temperature=0.7 的取舍：对话场景要一定创造性（解释、补全、联想），但起名/摘要等需要稳定的
    # 子任务用独立 ChatDeepSeek 实例降到 0.3（见 chat.py _generate_title / compress.py）。
    # streaming=True 是 astream 逐 token 产出的前提——不开就只能在 ainvoke 一次性拿完整结果。
    def initialize(self, base_dir: Path) -> None:
        """Initialize LLM (DeepSeek) and tools. Called once at startup."""

        # 项目根，后续 _build_agent / RAG 都用它
        self._base_dir = base_dir  
        # 组装 5 大工具（terminal/python_repl/fetch_url/read_file/search_knowledge）
        self._tools = get_all_tools(base_dir)

        # 延迟导入：放到函数内部，不用的时候不会加载langchain_deepseek包
        from langchain_deepseek import ChatDeepSeek  # 延迟 import

        # 实例化 DeepSeek 对话模型
        self._llm = ChatDeepSeek(  
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            api_base=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), 
            temperature=0.7,                    # 0.7：对话场景要一定创造性（起名/摘要等用独立实例降到 0.3）
            streaming=True,                     # 开流式：astream 才能逐 token 产出
        )

        # 初始化全局会话管理器，传入项目根目录，创建sessions文件夹
        session_manager.initialize(base_dir) 

        # 启动日志
        print(f"🤖 Agent initialized with {len(self._tools)} tools (DeepSeek: {os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')})")  

    # _build_agent（建 Agent）：每轮对话都重建——重读 prompt 文件 + create_agent。
    # 这是"改 SOUL/MEMORY 立即生效"的机制落点：不缓存 agent，每次现读现建。
    # 代价是每轮多几次文件 IO（本地微秒级），收益是热更新无需重启。
    #
    # 为什么不缓存 agent 实例：
    #   System Prompt 由六文件动态拼接（SOUL/IDENTITY/USER/AGENTS/MEMORY/技能快照），
    #   用户在前端 Monaco 改了 MEMORY.md 后，下一轮对话就要用新内容——若缓存 agent，
    #   改完得重启服务才生效。每轮重建牺牲微秒级 IO 换"改完立即生效"，值得。
    #
    # RAG 模式分支：build_system_prompt 内部按 rag_mode 决定拼不拼 MEMORY.md 全文。
    def _build_agent(self):
        """Build a fresh agent with current system prompt (re-reads files each time)."""
        from langchain.agents import create_agent  # LangChain 1.x 标准 API（禁用旧版 AgentExecutor）

        # 防御：未 initialize 就调用
        assert self._base_dir is not None  
        # 防御：LLM 未建
        assert self._llm is not None  

        # 读取配置，获取rag_mode开关（是否开启MEMORY.md长期记忆检索）
        rag_mode = get_rag_mode()

        # 调用PromptBuilder，读取6个markdown组件，拼接完整system prompt
        system_prompt = build_system_prompt(self._base_dir, rag_mode=rag_mode)

        # 建新 agent（每轮一个新实例）
        agent = create_agent(  
            model=self._llm,              # 复用同一个 LLM 实例
            tools=self._tools,            # 复用同一套工具
            system_prompt=system_prompt,  # 现读的 System Prompt
        )

        # 返回配好的 agent
        return agent  

    # _build_messages（转消息）：把 会话管理器保存的「字典格式对话历史」转成 LangChain 消息列表。
    # 认 user/assistant/system 三种角色（load_session_for_agent 已合并连续 assistant）。
    #
    # 为什么认三种角色：
    #   user/assistant 是对话主体，要求严格交替——连续两条 assistant 会被某些模型拒绝，
    #   load_session_for_agent 已把分段保存的多条 assistant 合并回一条，保证干净交替。
    #   system 用于承载压缩摘要（compressed_context）——以 system 消息注入头部，模型当作背景记忆参考。
    def _build_messages(self, user_message: str, history: list[dict[str, Any]]) -> list:
        """Convert session history + new message into LangChain messages."""

        # 收集 LangChain 消息
        messages = []  

        # 遍历从session读取的历史消息数组（里面每一条都是普通dict）
        for msg in history:

            # 取出role角色
            role = msg.get("role", "") 
            # 取出消息文本内容
            content = msg.get("content", "")
            
            if role == "user":  # 用户消息
                messages.append(HumanMessage(content=content))  # 转 HumanMessage
            elif role == "assistant":  # 助手消息
                messages.append(AIMessage(content=content))  # 转 AIMessage
            elif role == "system":  # 系统消息（压缩摘要注入）
                messages.append(SystemMessage(content=content))  # 转 SystemMessage

        # 把当前用户最新输入，追加到消息列表末尾
        messages.append(HumanMessage(content=user_message))

        # 返回消息列表给 agent.astream/ainvoke
        return messages  

    # astream（流式对话）：是对外暴露的异步流式对话入口，前端页面发起对话请求，调用这个函数；它是整个 Agent 运行的顶层驱动
    # 核心方法。api/chat.py event_generator 调此拿事件流。
    # 产出的 6 类事件（chat.py 翻译成 SSE）：
    #   retrieval（RAG 检索结果）/ token（逐字）/ new_response（分段边界）
    #   tool_start / tool_end / done
    #
    # 双流解析（stream_mode=["messages","updates"]）：
    #   messages 流 → AIMessageChunk 逐字 → yield token
    #   updates 流 → 按节点给工具事件：
    #     model 节点的 tool_calls → yield tool_start
    #     tools 节点的结果 → yield tool_end + 置位 tools_just_finished
    #   下一个非工具的 token 到达 + tools_just_finished → yield new_response（分段边界）
    #
    # RAG 注入：RAG 开时检索记忆 top-3，拼成 [记忆检索结果] 以 assistant 身份追加在历史尾部（不污染 System Prompt）。
    async def astream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream agent response with token-level and node-level events.

        Yields events:
          {"type": "retrieval", "query": "...", "results": [...]}  (RAG mode only)
          {"type": "token", "content": "..."}
          {"type": "tool_start", "tool": "...", "input": "..."}
          {"type": "tool_end", "tool": "...", "output": "..."}
          {"type": "done", "content": "..."}
        """
        # ── RAG 检索：RAG 开时检索记忆片段，拼成上下文 + yield retrieval 事件 ──
        #
        # RAG 注入流程：
        #   1. get_rag_mode() 现读开关（前端切换后下轮即生效）
        #   2. indexer.retrieve(message) 检索 top-3 记忆片段（内部 _maybe_rebuild 检测 MEMORY.md 变更）
        #   3. 有结果 → yield retrieval 事件（chat.py 翻译成 SSE 给前端渲染紫色卡片）
        #   4. 拼成 [记忆检索结果] 标记的上下文，以 assistant 身份追加在历史尾部
        #
        # 为什么以 assistant 身份追加而非塞进 System Prompt：
        #   System Prompt 是 Agent 的"出厂设定"，塞检索结果会污染它（每轮都变）。
        #   以 assistant 消息追加，LLM 会当成"之前助手提过的记忆"理解——更自然，也不破坏 Prompt 稳定性。

        # 读取全局配置，判断是否开启长期记忆 RAG 检索模式
        rag_mode = get_rag_mode() 

        # 用来存放检索出来的记忆片段文本
        rag_context = ""  

        # 如果开启RAG，并且项目根目录有效
        if rag_mode and self._base_dir:
            # 延迟导入MemoryIndexer单例获取函数，不开RAG就不会加载这个模块
            from graph.memory_indexer import get_memory_indexer

            # 获取全局唯一MemoryIndexer实例
            indexer = get_memory_indexer(self._base_dir)  # 拿单例索引器

            # 根据用户当前提问，检索MEMORY.md长期记忆，返回相关片段列表
            results = indexer.retrieve(message)

            # 检索到内容
            if results:  

                # yield retrieval 事件：把检索事件推送给前端，前端可以展示【正在检索记忆+检索到的片段】
                yield {  
                    "type": "retrieval",
                    "query": message,
                    "results": results,
                }

                # 遍历检索结果，拼接成一段格式化文本，带上序号、相似度分数、原文
                # 拼成 [片段1]...[片段2]... 的上下文
                snippets = "\n\n".join(  
                    f"[片段 {i+1}] (score: {r['score']})\n{r['text']}"
                    for i, r in enumerate(results)
                )

                # 包成 [记忆检索结果] 标记的上下文，作为记忆检索结果
                rag_context = f"[记忆检索结果]\n{snippets}"  

        # 每次流式请求，重新构建agent实例（会重读prompt文件，拿到最新system prompt）
        agent = self._build_agent() 

        # augmented_history 是 history 的副本 + 可选 RAG 上下文，不修改原始history变量
        augmented_history = list(history) 

        # 如果检索到记忆片段，追加一条assistant类型消息，把记忆注入对话上下文
        if rag_context: 
            # 以 assistant 身份追加（LLM 会当成"之前助手提过的记忆"理解）
            augmented_history.append(  
                {"role": "assistant", "content": rag_context}
            )

        # 调用前面 _build_messages：把字典历史消息，转为LangChain消息对象列表
        messages = self._build_messages(message, augmented_history)

        # 用来累积完整的AI最终回答字符串，最后一次性返回
        full_response = ""
        # 标记：上一步是不是刚执行完工具，用来控制前端换行/分割提示
        tools_just_finished = False 

        # ── 双流订阅：一次 astream 同时拿字级 token 与节点级工具事件 ──
        #
        # 双流事件对照（mode → 处理 → 产出事件）：
        #   messages 流：
        #     AIMessageChunk（有 content 无 tool_calls）→ 累积 full_response + yield token
        #       └ 若 tools_just_finished=True → 先 yield new_response 再 yield token
        #   updates 流（data = {node_name: node_data}）：
        #     model 节点 + tool_calls → yield tool_start（每个 tool_call 一个）
        #     tools 节点 + messages  → yield tool_end（每个工具结果一个）+ 置位 tools_just_finished
        #
        # 分段边界（new_response）的产生时机：
        #   tools 节点跑完 → tools_just_finished=True
        #   下一个 messages 流的 AI 文本块到达 → 检测到标志位 → yield new_response + 复位
        #   这就是"工具回合结束、新回答开始"的信号源，chat.py 据此新开 assistant 气泡。

        # agent.astream：LangChain异步流式执行agent
        # stream_mode=["messages", "updates"] 同时开启两种事件流：消息流 + 节点更新事件流
        async for event in agent.astream(
            {"messages": messages},                # 入参：消息列表
            stream_mode=["messages", "updates"],   # 双流：messages 字级 + updates 节点级
        ):
            
            # 多 stream_mode 下 event 是 元组 (mode, data) → ("messages", 消息数据) 或者 ("updates", 更新数据)
            # 单 mode 下 data 直接是事件
            if isinstance(event, tuple):  # 双流：元组
                # 拿到每一个event，要解包之后区分是 messages 流 还是 updates流
                mode, data = event
            else:  # 单流兼容（理论上不会进，但留兜底）
                mode = "messages"
                data = event

            # 分支 1：处理模型输出文字（打字机 token） → (token, metadata) 元组
            #  举例子：
            #  模型输出："1+1等于2"
            #  会拆成多个小块 event，依次 yield token事件："1"、"+"、"1"、"等于"、"2"
            if mode == "messages":
                msg, metadata = data  # msg 是消息块，metadata 是节点元信息

                # 检查这个消息对象有没有content，并且内容不为空
                if hasattr(msg, "content") and msg.content:
                    # 大模型一小块一小块持续吐出文本，每一块就是AIMessageChunk（非工具调用块）
                    if msg.type == "AIMessageChunk" or msg.type == "ai":  
                        # 有文本且非工具调用
                        if msg.content and not getattr(msg, "tool_calls", None):  

                            # 分段边界检测：工具刚跑完 + 现在开始新文本 → yield new_response
                            # tools_just_finished 标记：上一步是不是刚跑完工具
                            if tools_just_finished:
                                # 刚刚跑完工具，LLM 准备开始输出新一轮文字回答，开启一段全新的 AI 回答片段（segment）
                                yield {"type": "new_response"}  # 通知 chat.py 新开气泡
                                tools_just_finished = False  # 复位标志位

                            # 把小块文本拼到full_response，最后做完整记录
                            full_response += msg.content

                            # 推给前端，前端拿到这个事件，追加文字，实现打字机
                            yield {"type": "token", "content": msg.content}

            # 分支 2：捕获节点状态，抓工具调用的开始和结束
            #  updates 流是 LangGraph 图的节点状态。Agent 本质是一张图，里面有两个核心节点：
            #   - model节点：大模型思考，输出决定（要不要调用工具）
            #   - tools节点：执行工具，拿到工具返回结果
            # updates 数据是 {node_name: node_data}，
            elif mode == "updates":

                # 安全判断：data必须是字典类型，防止异常数据报错
                # data是字典，key是节点名字：model / tools，value是节点数据
                if isinstance(data, dict):  
                    # 遍历所有节点，只看 model 和 tools 节点
                    for node_name, node_data in data.items():

                        # --------------------------
                        # 情况A：节点名字 == tools 工具节点
                        # 含义：工具已经执行完毕，拿到工具返回结果
                        # --------------------------
                        if node_name == "tools" and "messages" in node_data:
                            # 遍历工具节点返回的消息列表，每一条tool_msg是工具返回的消息对象
                            for tool_msg in node_data["messages"]:
                                # 判断这个消息对象是否存在name属性（工具消息才有name，代表工具名称）
                                if hasattr(tool_msg, "name"):
                                    # 向外yield tool_end事件，推送给前端，表示工具执行完毕
                                    yield {  # yield tool_end（chat.py 给思考链卡片打勾）
                                        "type": "tool_end",                      # 事件类型：工具执行结束
                                        "tool": tool_msg.name,                   # 工具名字，例如 read_file / terminal
                                        "output": str(tool_msg.content)[:2000],  # 工具输出结果截 2000 字防爆
                                    }
                            # 标记：刚刚跑完工具！
                            # 后面模型输出文字的时候，会用这个标记，给前端发送new_response做UI分隔
                            tools_just_finished = True

                        # --------------------------
                        # 情况B：节点名字 == model 模型节点
                        # 含义：大模型思考完成，输出内容。如果包含tool_calls，代表模型决定调用工具
                        # --------------------------
                        elif node_name == "model" and "messages" in node_data:
                            # 遍历模型节点输出的消息
                            for agent_msg in node_data["messages"]: 

                                # 判断消息对象存在tool_calls属性，并且tool_calls不为空
                                # 有tool_calls = 模型想要调用工具，不是普通文字回答
                                if hasattr(agent_msg, "tool_calls") and agent_msg.tool_calls:
                                    # 遍历每一条工具调用指令（支持一次调用多个工具）
                                    for tc in agent_msg.tool_calls: 
                                        yield {
                                            "type": "tool_start",                # 事件类型：工具即将开始执行
                                            "tool": tc["name"],                  # 要调用的工具名字
                                            "input": str(tc.get("args", ""))[:1000],  # 调用工具的入参截 1000 字防爆
                                        }

        # Agent全部循环结束，推送done事件，附带完整回答文本
        yield {"type": "done", "content": full_response}

    # ainvoke（非流式调用）：一次性调用并返回完整回答。chat.py stream=false 时走此（基本不用，留作调试）。
    # 从结果消息里倒序找首条 AI 消息返回（倒序因为最后一条才是本轮回答）。
    #
    # 与 astream 的区别：
    #   - astream：流式，逐 token + 工具事件 + 分段保存（每段一条 assistant 消息）
    #   - ainvoke：一次性，不分段，user + assistant 各存一条
    # ainvoke 不走 RAG 检索（简化路径），若要 RAG 走 astream。
    async def ainvoke(self, message: str, session_id: str) -> str:
        """Non-streaming invocation (fallback)."""

        # 从会话管理器读取这个session_id对应的历史对话（list[dict]）
        history = session_manager.load_session(session_id)

        # 构建全新agent实例（重读system prompt，加载最新配置）
        agent = self._build_agent()

        # 把历史字典消息 + 用户当前消息，转换成LangChain消息对象列表
        messages = self._build_messages(message, history)

        # 异步调用agent，一次性执行完整Agent循环（思考、调用工具、拿到最终结果）
        # await 等待整个Agent全部跑完，才会继续往下执行代码
        result = await agent.ainvoke({"messages": messages})

        # 从返回结果取出所有消息列表
        final_messages = result.get("messages", [])

        # reversed 倒序遍历消息列表：从最后一条消息往前找（最新消息在末尾）
        for msg in reversed(final_messages):

            # 判断：消息有content、是AI消息、content不为空
            if hasattr(msg, "content") and msg.type == "ai" and msg.content:
                # 取回答
                response = msg.content  
                # 保存 用户这一轮提问/AI生成的回答 到会话JSON
                session_manager.save_message(session_id, "user", message)
                session_manager.save_message(session_id, "assistant", response)

                # 返回AI完整回答，函数结束
                return response

        # 如果遍历完没找到AI消息，返回兜底文本（异常情况）
        return "No response generated."


# 模块级单例：app.py lifespan 调 initialize 装配，api/chat.py 调 astream/ainvoke 用
# 为什么用模块级单例而非 FastAPI 依赖注入：
#   - LLM 实例 + 工具列表在启动时装配一次，全程复用——依赖注入每次请求建新实例太浪费
#   - 单例状态（_llm/_tools）在进程内存，跨请求共享
# 多 worker 注意：每个进程各持一份 agent_manager（本项目本地单进程无碍）。
agent_manager = AgentManager()  # 单例实例