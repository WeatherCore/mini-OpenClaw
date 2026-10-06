# ============================================================
# prompt_builder.py — System Prompt 的"装配线"（glue layer）。
# 职责：把 6 份人类可读的 Markdown 文件按固定顺序拼成给 LLM 的 System Prompt。
# 这是"文件即记忆"理念的落点——改 Agent 人格 = 编辑 SOUL.md，改完立即生效（agent.py 每轮重建）。
# 两个硬约束：
#   ① 单个组件超 MAX_COMPONENT_LENGTH(20000) 字符自动截断 + 加 ...[truncated] 标记，防撑爆上下文；
#   ② RAG 模式下 MEMORY.md 不拼接，改为追加 RAG_GUIDANCE 说明（记忆改为按需检索注入）。
# 谁在用：graph/agent.py _build_agent 每轮对话调用一次，结果喂给 create_agent 的 system_prompt 参数。
# ============================================================
"""Prompt Builder — Assemble system prompt from 6 Markdown files."""

from pathlib import Path 

# 单个md组件文件最大读取字符上限，防止文件过大导致上下文爆掉
# 20k 是经验值——兼顾"大段记忆能进来"与"留给对话历史的 Token 额度"
MAX_COMPONENT_LENGTH = 20000

# _read_component（读单组件）：读一个 Markdown 文件并按需截断。
# 设计取舍：缺失文件返回空串（静默跳过，不抛异常）——某个 workspace 文件没建也不让服务起不来
def _read_component(path: Path) -> str:
    """Read a file, truncating if it exceeds MAX_COMPONENT_LENGTH."""

    # 文件不存在，返回空字符串，不中断整个prompt组装流程
    if not path.exists():
        return ""
    
    # 读取文件全部文本内容，utf-8编码
    content = path.read_text(encoding="utf-8")

    # 判断文本长度是否超出上限
    if len(content) > MAX_COMPONENT_LENGTH:
        # 超长截断 + 标记
        # LLM 看到 ...[truncated] 就知道这段记忆被裁了，不会误以为是完整内容
        content = content[:MAX_COMPONENT_LENGTH] + "\n...[truncated]"

    return content


# RAG_GUIDANCE（RAG模式专用提示文本）：RAG 开启时拼在 Prompt 末尾，告诉 Agent"你的记忆将以 [记忆检索结果] 形式出现"。
# 之所以单独抽常量而非内联，是因为这段文字稳定不变、且被 build_system_prompt 两处引用判断
RAG_GUIDANCE = """注意：长期记忆(MEMORY.md)已切换为RAG检索模式。
系统会根据用户的问题自动检索相关记忆片段并注入上下文。
如果检索到了相关记忆，它们会以"[记忆检索结果]"标记呈现在你的上下文中。"""

# build_system_prompt（装配主函数）：按 6 文件顺序拼接 System Prompt。
# 顺序不可乱：技能快照 → 人格 → 自我认知 → 用户画像 → 行为准则 → 长期记忆，由外到内、由抽象到具体，
# LLM 读到时上下文层层收窄，最后一段记忆正好对接本轮提问。
def build_system_prompt(base_dir: Path, rag_mode: bool = False) -> str:
    """Build the full system prompt by concatenating components in order.

    Order:
    1. SKILLS_SNAPSHOT.md — available skills listing
    2. workspace/SOUL.md — persona, tone, boundaries
    3. workspace/IDENTITY.md — name, style, emoji
    4. workspace/USER.md — user profile
    5. workspace/AGENTS.md — operation instructions & memory/skill protocols
    6. memory/MEMORY.md — cross-session long-term memory (skipped in RAG mode)

    When rag_mode=True, MEMORY.md is excluded and a RAG guidance note is appended.
    """

    # 前 5 个组件固定必拼：技能、人格、认知、画像、行为准则——构成 Agent 的"出厂设定"
    components = [
        ("Skills Snapshot", base_dir / "SKILLS_SNAPSHOT.md"),
        ("Soul", base_dir / "workspace" / "SOUL.md"),
        ("Identity", base_dir / "workspace" / "IDENTITY.md"),
        ("User Profile", base_dir / "workspace" / "USER.md"),
        ("Agents Guide", base_dir / "workspace" / "AGENTS.md"),
    ]

    # 第 6 个组件（长期记忆）按 RAG 模式分支：
    #  - False：把 MEMORY.md 全文拼进 Prompt（记忆少时最直接，但记忆一长就撑爆 Token）
    #  - True：不拼，改由 agent.py astream 检索 top-3 片段注入到对话历史尾部
    if not rag_mode:
        # RAG 关闭时才把 MEMORY.md 全文加进组件列表
        components.append(  
            ("Long-term Memory", base_dir / "memory" / "MEMORY.md")
        )

    # 存放每一段读取成功后的提示词片段
    parts: list[str] = []

    # 遍历所有组件，逐个读取md文件内容
    for label, path in components:
        # 读单组件（缺失返空串、超长已截断）
        content = _read_component(path)  
        # 非空才加入片段列表（空文件直接跳过，不生成注释块）
        if content: 
            parts.append(f"<!-- {label} -->\n{content}")
            # 加上HTML注释标记，方便调试：<!-- 组件名称 -->，方便区分各个模块边界

    # 如果开启RAG模式，追加RAG模式说明文本作为一段组件
    # 告诉 Agent"记忆将以检索片段形式出现"，避免它找不到 MEMORY.md 全文而困惑
    if rag_mode:
        parts.append(f"<!-- RAG Mode -->\n{RAG_GUIDANCE}")  # 末尾追加 RAG 指引段落

    # 使用两个换行符把所有片段拼接成一个完整大字符串
    # \n\n 在markdown里代表分段，各个模块之间有间隔，模型更容易区分模块
    return "\n\n".join(parts)
