# ============================================================
# session_manager.py — SessionManager（会话持久化层的会话管理器，"文件即记忆"理念在会话侧的落点）
# 职责：把每一轮 Agent 的聊天记录、元信息，以独立 JSON 文件存在 `sessions/` 文件夹，提供增删改查 + 压缩 + Raw Messages。
# ============================================================
# 三大机制在此交汇：
#   ① v1→v2 自动迁移：_read_file 读到 plain list（旧格式）自动包装成带 metadata 的 v2 对象，
#      老会话文件无需手动迁移即可在新版后端跑通；
#   ② 压缩归档：compress_history 把前 N 条消息摘要后归档到 sessions/archive/，主文件留后半段 +
#      compressed_context 累积摘要——长会话不爆 Token 的关键；
#   ③ Agent 专用加载：load_session_for_agent 合并连续 assistant 消息（每轮工具调用会产生多条
#      assistant 消息，但 LLM 上下文要求 user/assistant 严格交替），并在头部注入压缩摘要。
# ============================================================
# 谁在用：
#   - graph/agent.py initialize 拿单例 session_manager；
#   - api/chat.py 每轮 done 时 save_message 落盘 + 首轮 _generate_title 后 update_title；
#   - api/sessions.py 7 个 HTTP 端点全转给它；
#   - api/compress.py 调 compress_history 归档压缩。
# 设计取舍：① 不引入数据库，纯文件持久化——会话躺在 sessions/ 目录打开即可看（透明性优先）；
#           ② 无内存缓存，每次现读盘——会话数少、调用频率低，可接受；好处是前端改完立即生效。
# ============================================================
"""Session Manager — JSON file-based conversation persistence with metadata."""

import json    
import time
from pathlib import Path 
from typing import Any 

# SessionManager（会话管理器）：单例模式，最后一行 session_manager = SessionManager() 全局实例，在 import 时创建
# 真正装配（_sessions_dir 填充）推迟到 agent.py initialize(base_dir) 调本类 initialize——
# 与 AgentManager 同样的"延迟填充"模式，避免 import 副作用依赖 base_dir。
class SessionManager:
    """Manages conversation history as JSON files in sessions/ directory.

    Storage format (v2):
    {
        "title": "会话标题",
        "created_at": 1706000000,
        "updated_at": 1706000100,
        "messages": [{"role": "user", "content": "..."}, ...],
        "compressed_context": "历史摘要（压缩长对话才会出现）"
    }
    """

    # __init__（初始化）：_sessions_dir 先只声明变量，等 initialize(base_dir) 时才填。
    # 延迟填充让模块级单例可以早创建，真正的路径绑定推迟到 base_dir 就位后（与 AgentManager 同模式）。
    def __init__(self) -> None:
        # 会话目录绝对路径，initialize 时填为 base_dir / "sessions"
        self._sessions_dir: Path | None = None  

    # 为什么不直接在 init 里面创建目录？ 
    # 因为创建 SessionManager 对象的时候，还不知道项目根目录在哪里，需要等主程序加载完配置之后再初始化路径

    # initialize（启动装配）：agent.py AgentManager.initialize 调一次。
    # 职责单一：绑定会话目录 + 确保目录存在（首次启动 sessions/ 不存在时 mkdir 兜底）。
    def initialize(self, base_dir: Path) -> None:

        # 接收项目根路径，拼接出 `sessions` 目录，会话文件全部落在 backend/sessions/
        self._sessions_dir = base_dir / "sessions"
        # 文件夹不存在就新建；已经存在不会抛异常
        self._sessions_dir.mkdir(exist_ok=True)

    # _session_path（私有）：把 session_id 映射成磁盘文件路径
    # 安全设计：safe_id 过滤掉非 [alnum/-_] 字符——防止 ../ 路径穿越（session_id 来自 URL，不可信）。
    # 例如 session_id="../etc/passwd" → safe_id="" → 落到 sessions/.json（无害空文件名）。
    def _session_path(self, session_id: str) -> Path:

        # 防御性断言：确保一定先调用过 `initialize()`。如果没初始化就调用，直接崩溃提醒开发者
        assert self._sessions_dir is not None  

        # 仅保留字母数字与 -_，其余字符全删 —— session_id 是 UUID 生成 + 用户标题改名，正常情况无特殊字符
        safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")
        # 拼接成文件路径：`sessions/{safe_id}.json`，一个会话 = 一个 JSON 文件
        return self._sessions_dir / f"{safe_id}.json"

    # _read_file（私有）：读取会话文件，并且自动做格式升级，统一转为 v2 dict。
    # 关键机制—— v1→v2 自动迁移：早期版本会话文件是 plain list（[{"role":...}, ...]），
    #   新版要求 dict 带 metadata。读到 list 时自动包装成 v2 对象返回，老会话无需手动迁移。
    # 容错设计：文件不存在/JSON 解析失败一律返回 {}——上层据此判定"会话不存在"走新建流程，不抛异常中断。
    def _read_file(self, session_id: str) -> dict[str, Any]:
        """Read session file and normalize to v2 format."""

        # 根据session_id拿到文件路径
        path = self._session_path(session_id)

        # 会话文件不存在（新会话 / 已删除）→ 返回空 dict
        if not path.exists():  
            return {}
        
        try:  
            # 读取文件文本，用json.loads转成python对象
            data = json.loads(path.read_text(encoding="utf-8"))

            # ==========【重点：旧版本格式兼容】==========
            # 老版本mini OpenClaw会话文件直接存消息列表： [{"role":"user",...}]
            # 新版本v2是字典 {"title":"xxx", "created_at":..., "messages":[]}
            # 判断：如果读出来data是list，代表是旧格式，自动包装成新标准v2字典
            if isinstance(data, list): 
                # 迁移策略：读时迁移不落盘 —— 只把 v1 在内存里包装成 v2 返回，原文件保持 v1 不动。
                # 这是性能与一致性权衡：
                # ① 避免读会话时意外触发写盘（IO 成本 + 锁风险）；
                # ② 下次 _write_file 自然会以 v2 格式覆盖原文件（save_message / rename 等任何写操作触发）。
                # 这种"懒迁移"让升级后端版本无需批量转换会话文件——首次读自动包装，首次写自动落定。

                # 迁移时刻作为 updated_at
                now = time.time()  
                return {  # 包装成 v2 对象返回，不落盘（下次 _write_file 时自然写成 v2）
                    "title": session_id,  # v1 没标题，用 session_id 兜底（后续 AI 起名会覆盖）
                    "created_at": path.stat().st_ctime,  # 获取文件系统层面的文件创建时间戳（unix 秒）
                    "updated_at": now,
                    "messages": data,  # 原消息列表原样保留
                }
                # 这里格式升级没预留 compressed_context 空字段，是有意为之，不是漏了。核心理由四条：
                # 1. dict.get 兜底已经让"空字段"和"字段不存在"完全等价
                #  所有读 compressed_context 的地方都这么写：existing_context = data.get("compressed_context", "")，字段不存在 → get 返回空串/None → 逻辑上等价于"空字段"。代码行为零差异。那预填一个 "compressed_context": "" 纯属形式主义，没有实际收益
                # 2. 透明性优先 —— 这文件是给人看的
                #  这个项目的头号设计取舍就是"文件即记忆，打开即可看"。一个没压缩过的会话（绝大多数会话永远不会被压缩），打开看到 "compressed_context": "" 反而让人困惑——"这字段干嘛的？为什么是空的？"。按需出现的语义更清晰：看到这个字段 = 这个会话被压缩过，字段本身就是信号  
                # 3. 一致性——和 tool_calls 同一套约定 
                #  在 save_message() 中写着 “if tool_calls:  # 有工具调用才加 tool_calls 字段”，tool_calls 也是"有才加，没有不加"。整个文件的可选字段都遵循同一哲学：按需出现，不预填空值。compressed_context 如果破例预填，反而不一致

            # 如果已经是字典（新版v2格式），直接原样返回
            return data  

        # 捕获两类异常
        # json.JSONDecodeError：文件存在，但json语法损坏（比如中途断电保存一半文件）
        # Exception：兜底所有其他错误（权限不足等）
        except (json.JSONDecodeError, Exception):
            # 出错返回空字典，不会直接崩溃程序
            return {}

    # _write_file（私有）：写会话文件。每次写都更新 updated_at（mtime 维护）
    # 入参 `data` 必须是 v2 标准字典（`title/created_at/updated_at/messages`）
    def _write_file(self, session_id: str, data: dict[str, Any]) -> None:
        """Write session data to file."""

        # 强制更新最后修改时间：每次写入，不管改了什么，都刷新updated_at
        data["updated_at"] = time.time()
        # 获取会话文件路径
        path = self._session_path(session_id)

        # 把python字典序列化为json字符串，写入文件
        # ensure_ascii=False：中文不转成\uXXXX，json里面直接显示中文
        # indent=2：格式化缩进，方便人工打开json调试    
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # create_session（新建会话）：新建空白会话，生成 v2 标准数据结构，调用`_write_file`落地保存
    # api/sessions.py POST /sessions 调
    # 返回 metadata（不含 messages，前端列表只需 id+title+时间戳）。
    # 默认标题 "New Chat" —— 首轮对话后 chat.py 的 _generate_title 会改成 AI 生成标题。
    def create_session(self, session_id: str) -> dict[str, Any]:
        """Create a new empty session. Returns metadata."""

        # 设计取舍：建会话即落盘——即返回前端列表立即可见，避免"先列后建"的两次 IO 往返。
        # 即便用户秒级新建多个会话又不点进去，也只是产生几个空会话文件——可后续清理或忽略。

        now = time.time()
        # v2 会话对象骨架——空 messages 等首轮对话 save_message 追加
        data: dict[str, Any] = {  
            "title": "New Chat",  # 占位标题，待首轮对话后 AI 起名覆盖
            "created_at": now,
            "updated_at": now,
            "messages": [], 
        }

        # 落盘（建空文件，前端列表立即可见）
        self._write_file(session_id, data)  

        # 返回 metadata（前端列表项所需字段；messages 不返回，省传输）
        # 不返完整 data 的理由：前端列表只需展示 id+title+时间戳，messages 留到点开时 load_session 拉
        return {"id": session_id, "title": data["title"], "created_at": now, "updated_at": now}

    # load_session（加载消息列表）：读取会话，只返回 messages 消息列表，用于 UI 展示对话记录，前端展开历史会话时调
    # 只返回 messages 数组（不含 metadata）——前端渲染聊天气泡只需要消息序列。
    def load_session(self, session_id: str) -> list[dict[str, Any]]:
        """Load conversation history for a session."""

        # 读 + 兼容旧格式，拿到v2字典
        data = self._read_file(session_id)  
        # 空会话（不存在 / 损坏）→ 返回空列表
        if not data:  
            return []

        # 从字典取出messages字段，没有messages键就返回空列表
        return data.get("messages", [])

    # save_message（追加消息）：api/chat.py 每轮对话 done 时调，user/assistant 消息都走这里。
    # 关键设计：
    # ① 会话不存在自动创建兜底（防首次对话未先 POST /sessions 的边界场景）；
    # ② tool_calls 可选字段——只有 assistant 工具调用消息才带，普通消息不存省空间。
    # 项目场景：
    #  - 用户发消息 → `save_message(role="user", content="xxx")`
    #  - LLM 回复或者生成工具调用 → `save_message(role="assistant", content="...", tool_calls=[...])`
    def save_message(
        self,
        session_id: str,
        role: str,  # "user" / "assistant"（System Prompt 不存盘，每轮重建）
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,  # 工具调用记录（仅 assistant 工具消息）
    ) -> None:
        """Append a message to the session history."""

        # 读取当前会话完整数据（自动兼容旧版本）
        data = self._read_file(session_id) 

        # 会话不存在 → 自动创建兜底（与 create_session 同结构）
        if not data:

            now = time.time()
            data = {  # 兜底骨架（与 create_session 同结构，避免首次对话未先 POST /sessions 的边界崩）
                "title": "New Chat",
                "created_at": now,
                "updated_at": now,
                "messages": [],
            }  
            # 为什么不直接调 create_session 复用这块骨架？两重不划算：
            # ① 契约不对齐——create_session 返回 metadata（id+title+时间戳，不含 messages），
            #    而本方法紧接着要 data["messages"].append(msg)，用其返回值会 KeyError；
            # ② 双写盘——create_session 内部 _write_file 落盘一次空会话，本方法末尾还要 _write_file 一次，
            #    复用等于一次 save 触发两次磁盘写，纯浪费 IO。
            # 手写 6 行骨架 < 改契约 + 多一次 IO 的成本，故就近内联。

        # 构建单条消息基础结构
        msg: dict[str, Any] = {"role": role, "content": content} 
        
        # 如果存在工具调用信息，追加tool_calls字段（LangChain工具调用的关键）
        if tool_calls:
            # 把工具调用记录挂到消息上（仅 `assistant`消息：大模型输出 才会有，用户提问，不会有 tool_calls）
            msg["tool_calls"] = tool_calls  

        # 把这条新消息追加到消息数组末尾
        data["messages"].append(msg)

        # 调用底层写入落盘，自动刷新updated_at
        self._write_file(session_id, data)

    # rename_session（重命名）：api/sessions.py PUT /sessions/{id} 调。
    # 会话不存在抛 FileNotFoundError——与前端"重命名一个已删除会话"场景对应，让 API 层返 404。
    def rename_session(self, session_id: str, title: str) -> None:
        """Rename a session."""

        # 读现有会话（含自动迁移）
        data = self._read_file(session_id)  

        # 会话不存在 → 抛错让 API 层返 404（重命名已删除会话是异常场景）
        # 和 save_message 不一样：会话不存在的时候直接抛错，不会自动创建会话
        if not data:  
            # 让 API 层转 404，不静默吞错，让前端提示“重命名一个已删除会话”。
            raise FileNotFoundError(f"Session {session_id} not found")  

        # 修改title字段
        data["title"] = title
        # 写入磁盘，自动更新updated_at
        self._write_file(session_id, data)

    # 语义化编程
    # update_title（更新标题）：别名函数（alias），本身没有任何业务逻辑，只是直接调用rename_session
    # 存在原因：chat.py _generate_title 后调 update_title（语义是"AI 自动起名"），
    #           sessions.py 用户手动改名调 rename_session——同一动作两个语义入口，便于代码可读。
    def update_title(self, session_id: str, title: str) -> None:
        """Update session title (alias for rename_session)."""
        self.rename_session(session_id, title)  # 直接转发，不重复实现

    # delete_session（删除会话）：api/sessions.py DELETE /sessions/{id} 调。
    # 容错：文件不存在静默通过（幂等删除——重复点删除不报错）。
    # [注意] 不删 archive/ 下的归档文件——归档是历史快照，主会话删除不连带删归档（保留审计轨迹）。
    def delete_session(self, session_id: str) -> None:
        """Delete a session file."""

        # 获取会话文件路径
        path = self._session_path(session_id)
        # 存在才删（幂等）—— 重复点删除不报错
        if path.exists():  
            # 删除文件（不删 archive/ 归档，保留审计轨迹）
            path.unlink() 

    # get_raw_messages（取原始会话数据）：api/sessions.py GET /sessions/{id}/messages 调。
    # 返回完整 data（含 title + 全部消息 + compressed_context）——Raw Messages 面板给开发者看完整 LLM 入参。
    # 与 load_session 区别：load_session 只返 messages 数组（前端渲染用）；本方法返完整的会话字典（元信息 + 消息）
    # 使用场景：需要一次性拿到标题、创建时间、所有消息的场景
    def get_raw_messages(self, session_id: str) -> dict[str, Any]:
        """Return the complete session data including all messages."""

        # 读取会话文件，并且自动做格式升级，统一转为 v2 dict
        data = self._read_file(session_id)

        if not data:  # 不存在 → 返回空骨架（前端面板不崩）
            return {"title": "", "messages": []}

        # 完整返回（含 compressed_context 等所有字段）——给开发者看完整 LLM 入参
        return data  

    # list_sessions（会话列表读取，前端首页用）：api/sessions.py GET /sessions 调，前端 Sidebar 渲染会话列表
    # 扫描 sessions 目录下所有 .json 会话文件，读取会话元信息，按「最后修改时间倒序」返回会话列表，给前端展示会话列表用
    # 排序：按文件 mtime 倒序（最近修改在前）——_write_file 每次刷 updated_at，但 mtime 是文件系统层，
    #       即便 updated_at 字段缺失也能正确排序。
    # 容错：单个会话文件 JSON 损坏时用文件名 stem 兜底标题 + mtime 兜底时间——不让一个坏文件让整个列表崩。
    def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions with metadata."""

        # 防御性断言：必须先执行initialize初始化目录，否则直接报错
        assert self._sessions_dir is not None  

        # 准备空列表，用来存放所有会话的元数据
        sessions: list[dict[str, Any]] = []

        # 先收集所有 .json 文件 + 安全取 mtime。
        # 为什么不直接放 sorted 的 key 里：原写法 key=lambda p: p.stat().st_mtime 没有 try 保护，
        # 一个文件 stat 失败（并发删 / 权限丢）会让整个 sorted 炸、列表全崩——和循环体的容错哲学矛盾。
        files = []

        for f in self._sessions_dir.glob("*.json"):
            try:
                # f.stat() 获取文件系统属性；st_mtime = 文件在操作系统层面的最后修改时间戳（秒级浮点数）
                mtime = f.stat().st_mtime
            except OSError:
                # 异常兜底：文件损坏/权限不足/文件被中途删除，拿不到修改时间，就设为0
                mtime = 0.0

            # 把 (修改时间, 文件路径对象) 打包成元组存入列表
            files.append((mtime, f))

        # 按 mtime 倒序排（最新修改在前），符合聊天软件习惯：最近聊天在最上面
        # 用文件 mtime 而非 data["updated_at"] 排序的取舍：updated_at 可能被人工改 / 字段缺失，
        # 文件系统 mtime 由 OS 维护更可靠；缺点是用户改会话文件内容 mtime 也会变（但场景对排序影响小）。
        files.sort(key=lambda x: x[0], reverse=True)

        for mtime, f in files:

            # 默认值（v1 纯列表 / JSON 损坏 / stat 异常 都走这套兜底）——提前设好消除原 else/except 重复
            title = f.stem
            # 复用已缓存的 mtime，不重复调 f.stat()
            updated_at = mtime  

            try:
                # 读取json文件原始文本，解析成python对象
                raw = json.loads(f.read_text(encoding="utf-8"))
                # 只有新版 v2 dict 才覆盖默认值（v1 纯列表 / 其他格式保持默认）
                if isinstance(raw, dict):
                    title = raw.get("title", f.stem)
                    updated_at = raw.get("updated_at", mtime)

            # JSON 损坏 / 读取异常 → 保持上面的默认值（f.stem + mtime）
            except Exception:
                pass  

            # 组装会话元数据字典，加入返回列表
            sessions.append({  # 列表项：id（文件名 stem）+ title + updated_at
                "id": f.stem,  # session_id = 文件名（不含 .json）——前端据此调其他端点
                "title": title,
                "updated_at": updated_at,
            })

        # 返回会话列表，给到上层接口，用于前端渲染会话侧边栏
        return sessions

    # compress_history（压缩历史）：api/compress.py POST /sessions/{id}/compress 调。
    # 执行压缩归档业务逻辑（写操作），并返回压缩后的会话元数据。
    # 核心机制——归档 + 摘要累积：
    #   ① 前 N 条消息移到 sessions/archive/{session_id}_{timestamp}.json（历史快照，不删只归档）；
    #   ② 主文件 messages 删掉前 N 条，留后半段继续对话；
    #   ③ summary 追加到 compressed_context（多次压缩用 "\n---\n" 分隔累积）——load_session_for_agent
    #      会把它注入对话头部，让 LLM 保留之前被压缩的上下文。
    # 这是长会话不爆 Token 的关键：50 条对话压成 500 字摘要，Token 占用从 ~10k 降到 ~500。
    def compress_history(
        self, session_id: str, summary: str, num_to_remove: int  
        # summary=DeepSeek 生成的 ≤500 字摘要；
        # num_to_remove=要归档的消息条数（compress.py 取 max(4, len//2)）
    ) -> None:
        """Archive first N messages and store summary as compressed_context."""

        # 断言：必须先初始化session目录，否则直接报错
        assert self._sessions_dir is not None
        # 读取当前会话完整json数据
        data = self._read_file(session_id)
        # 会话不存在 → 静默返回（压缩一个不存在的会话无意义）
        if not data:  
            return

        # 取出消息列表，没有messages键就默认空列表
        messages = data.get("messages", [])
        # 切片前 N 条作为归档内容
        archived_messages = messages[:num_to_remove]  

        # 构建归档文件夹路径：sessions/archive/，与主会话文件隔离（list_sessions glob 不会扫到）
        # 为什么归档而不直接删：保留历史快照用于审计/回溯——压缩后用户仍可去 archive/ 翻看被压掉的内容。
        # 文件名带时间戳防止同会话多次压缩覆盖；不删 archive 是为了保留完整对话轨迹。
        archive_dir = self._sessions_dir / "archive"
        archive_dir.mkdir(exist_ok=True)

        # 组装归档文件内容（带 session_id + 时间戳，便于审计追溯）
        archive_data = { 
            "session_id": session_id,          # 归属哪个会话
            "archived_at": time.time(),        # 归档时刻
            "messages": archived_messages,     # 被切出来的旧原始消息
        }

        # 归档文件名：{session_id}_{timestamp}.json —— 同一会话多次压缩产生多个归档文件（历史快照链）
        archive_path = archive_dir / f"{session_id}_{int(time.time())}.json"

        # 写入归档json文件
        archive_path.write_text(
            json.dumps(archive_data, ensure_ascii=False, indent=2),  # 中文原样 + 美化（与主会话同格式）
            encoding="utf-8",
        )

        # 主文件 messages 切掉前 N 条，留后半段在主会话继续对话 —— 这是"压缩"的实际效果
        data["messages"] = messages[num_to_remove:]

        # Append summary to compressed_context (support multiple compressions)
        # 多次压缩累积：已有 compressed_context 时用 "\n---\n" 分隔追加（保留多次摘要的层级）；
        #              首次压缩直接写入。load_session_for_agent 会读这个字段注入对话头部。

        # 读取已经存在的压缩摘要
        existing_context = data.get("compressed_context", "")

        if existing_context:  # 已有摘要 → 分隔追加（多次压缩的累积链）
            data["compressed_context"] = existing_context + "\n---\n" + summary  # \n---\n 分隔多次摘要，保留层级
        else:                 # 首次压缩 → 直接写入
            data["compressed_context"] = summary  # 首次压缩直接写入 summary 字符串

        # 写回主会话json，更新截断后的messages和compressed_context
        self._write_file(session_id, data)

    # get_compressed_context（取压缩摘要）：读（读取已经存好的摘要，拿给 Agent 用）
    # 用途：api 层展示压缩历史时调；load_session_for_agent 内部也读这个字段（封装在本类里更内聚）。
    def get_compressed_context(self, session_id: str) -> str | None:
        """Return compressed context if any."""

        # 读取会话
        data = self._read_file(session_id)
        # 会话不存在 → 返回 None（无摘要）
        if not data:  
            return None

        # 取出compressed_context字段；键不存在返回None
        return data.get("compressed_context")

    # load_session_for_agent（Agent 专用加载）：graph/agent.py 每轮对话调，返回可直接喂 LLM 的消息序列。
    # 与 load_session 的两点关键差异：
    #   ① 合并连续 assistant 消息——chat.py 每轮工具调用后会产生多条 assistant 消息（分段保存），
    #      但 LLM 上下文要求 user/assistant 严格交替，连续 assistant 会让 LLM 困惑；
    #   ② 注入 compressed_context——若会话被压缩过，把摘要作为首条 system 消息注入头部，
    #      让 LLM 保留之前被压缩的上下文（"以下是之前对话的摘要"前缀让 LLM 识别这是历史摘要）。
    # 还有一个隐藏处理：strip 掉 tool_calls 字段——LLM 上下文不需要工具调用记录（那是 Agent 内部状态）。
    def load_session_for_agent(self, session_id: str) -> list[dict[str, Any]]:
        """Load session history merged for LLM context.

        Since we now save multiple consecutive assistant messages per turn,
        this method combines them back into single assistant messages to
        maintain proper user/assistant alternation for the LLM.

        If compressed_context exists, inserts it at the head as a system
        message so the LLM retains prior context.
        """

        # 这是 SessionManager 的"双重加工"方法——既要"反分段"（合并连续 assistant），
        # 又要"前置摘要注入"（compressed_context 作为首条 system）。两者结合后产出的
        # 消息序列可直接喂 LLM，调用方无需任何后处理。agent.py 每轮对话调一次此方法。
        # 注意：本方法不写盘——只读 + 内存加工。副作用零，可重复调用。

        # 读取会话
        data = self._read_file(session_id)
        # 取出原始消息数组；会话不存在就为空列表
        messages = data.get("messages", []) if data else []

        # merged：存放加工完成、准备喂给LLM的消息数组（user/assistant 严格交替）
        merged: list[dict[str, Any]] = []

        # 压缩摘要注入头部：让 LLM 知道之前聊过什么（被压缩的部分）。
        # 用 system 角色承载 —— 摘要作为长期记忆注入 system 层，模型当作背景知识参考。
        # 读取之前compress_history保存下来的历史摘要
        compressed = data.get("compressed_context", "") if data else ""
        # 有摘要才注入（未压缩的会话跳过）
        if compressed:  
            merged.append({  
                # 把压缩摘要作为首条 system 消息注入
                "role": "system",  
                # [以下是之前对话的摘要] 前缀让 LLM 识别这是历史摘要而非当前对话内容
                "content": f"[以下是之前对话的摘要]\n{compressed}",
            })

        # 遍历原始消息，合并连续 assistant 消息
        for msg in messages:
            # 判断条件：merged列表不为空，并且【上一条消息是assistant】，【当前这条msg也还是assistant】
            # 三个同时满足 = 连续两条 assistant
            # 只要一轮对话里 Agent 调用了工具，磁盘上就必然出现连续 assistant 消息
            if (
                merged
                and merged[-1]["role"] == "assistant"
                and msg["role"] == "assistant"
            ):
                # 用 \n 拼接多条 assistant 消息内容——LLM 看到的是一条完整的 assistant 回复
                merged[-1]["content"] += "\n" + msg["content"]
            else:
                # 非 assistant 连续场景：新起一条消息；只保留 role + content，丢 tool_calls（LLM 不需要）
                merged.append({"role": msg["role"], "content": msg["content"]})  # 新起一条消息（不含 tool_calls）

        # 返回可直接喂 LLM 的消息序列
        return merged  

    # get_message_count（取消息条数）：前端 Sidebar 显示消息数 / 判断是否可压缩时调。
    # 简单封装 _read_file + len——会话不存在返回 0。
    def get_message_count(self, session_id: str) -> int:
        """Return the number of messages in a session."""

        # 读会话（含自动迁移）
        data = self._read_file(session_id)  
        # 不存在 → 0 条
        if not data:  
            return 0
        # 返回原始消息的数量（磁盘上保存的messages条数，不是合并后的merged数量）
        return len(data.get("messages", []))


# 模块级单例：import 时即创建（无副作用），agent.py AgentManager.initialize 调 initialize(base_dir) 装配。
# 好处：全局共享_sessions_dir，不用到处 new SessionManager，避免多个实例各自读写文件，出现竞争、目录不一致问题
# 注意：刚创建实例的时候_sessions_dir=None，必须调用session_manager.initialize(base_dir)完成初始化，才能使用，前面代码里的 assert 就是校验这个
session_manager = SessionManager()