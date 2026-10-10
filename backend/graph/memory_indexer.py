# ============================================================
# memory_indexer.py — MEMORY.md 的向量索引器（RAG 记忆检索的引擎）。
# 职责：把 memory/MEMORY.md 切块向量化建索引，按用户问题检索 top-3 片段，供 Agent RAG 模式用。
# 核心机制——MD5 变更检测：
#   retrieve() 前先比 MEMORY.md 的当前 MD5 与上次建索引时存的 MD5：
#     - 相同 → 直接复用 storage/memory_index/ 持久化索引（零 Embedding 调用，省钱省时）；
#     - 不同 → rebuild_index() 重建 + 落盘 + 更新 hash。
#   这是"前端改 MEMORY.md → RAG 检索立即用新内容"闭环的引擎（files.py 保存触发 rebuild 也走这里）。
#
# 索引生命周期：
#   启动 app.py → rebuild_index() 首次建索引
#   对话 retrieve() → _maybe_rebuild() 比 hash → 变了才重建，没变复用
#   前端保存 MEMORY.md → files.py 触发 rebuild_index() 强制重建
#
# 分块参数：SentenceSplitter(chunk_size=256, chunk_overlap=32)
#   256 字符一块、相邻块重叠 32 字符——重叠防语义在块边界断裂。
#
# 降级策略：LlamaIndex 没装全 / Embedding 调用失败 → _index=None，retrieve 返回空列表，
#           Agent 拿到空检索结果照常工作（只是 RAG 模式退化成无记忆检索）。
# ============================================================
"""MemoryIndexer — Vector index for MEMORY.md with auto-rebuild on change."""

import hashlib                                       # MD5 变更检测
import os                                            # 读环境变量配 Embedding
from pathlib import Path                               # 路径拼接
from typing import Any                                 # _index 任意类型注解


# MemoryIndexer（记忆索引器）：单例。MD5 检测 + 切块向量化 + 持久化复用。
# 专门给 MEMORY.md 做向量索引的独立 RAG 索引管理器
class MemoryIndexer:
    """Indexes memory/MEMORY.md for RAG retrieval.

    Uses MD5 hash to detect changes and auto-rebuild the vector index.
    Storage is kept separate from the knowledge base index.
    """

    # __init__（初始化）：绑定四条路径——记忆源、索引存储、hash 文件、index 实例。
    def __init__(self, base_dir: Path) -> None:
        # 项目根目录
        self._base_dir = base_dir
        # 记忆文件路径 memory/MEMORY.md，人工/agent写入的长期记忆都在这里
        self._memory_path = base_dir / "memory" / "MEMORY.md"
        # 向量索引持久化存储目录，独立于知识库索引
        self._storage_dir = base_dir / "storage" / "memory_index"
        # .memory_hash 文件：用来保存上一次构建索引时，MEMORY.md的md5哈希值
        self._hash_path = self._storage_dir / ".memory_hash"
        # LlamaIndex 索引实例（懒加载，首次 retrieve 才填充）
        self._index: Any = None  

    # _get_file_hash（算当前 MD5）：读 MEMORY.md 字节算 MD5。文件不存在返回空串。
    def _get_file_hash(self) -> str:
        """Get MD5 hash of MEMORY.md."""

        # 如果MEMORY.md不存在，返回空字符串
        if not self._memory_path.exists():
            return ""
        # read_bytes：读取原始二进制，计算md5，不要用read_text，避免编码带来哈希偏差
        content = self._memory_path.read_bytes()
        # 返回 MD5 十六进制串
        return hashlib.md5(content).hexdigest()  

    # _get_stored_hash（读存档 MD5）：读上次建索引时存的 hash。没存过返回空串。
    def _get_stored_hash(self) -> str:
        """Get the stored hash from the last build."""

        # hash 文件不存在（首次/被清）
        if not self._hash_path.exists():  
            return ""  # 返回空串

        # 读存档 hash
        return self._hash_path.read_text(encoding="utf-8").strip()  

    # _save_hash（存 MD5）：建完索引后把当前 hash 存档，供下次比对。
    def _save_hash(self, hash_value: str) -> None:
        """Save the current hash."""

        # 先保证父目录存在
        self._hash_path.parent.mkdir(parents=True, exist_ok=True)
        # 保存新的md5哈希到.hash文件
        self._hash_path.write_text(hash_value, encoding="utf-8")

    # _maybe_rebuild（按需重建）：retrieve 前调用。当前 hash 与存档不同才重建。
    #
    # 为什么是"按需"而非"每次重建"：
    #   建索引要调 Embedding API（花钱+花时间），而 MEMORY.md 大多数时候没变。
    #   用 MD5 比对：没变就跳过，变了才重建——省钱省时，这是 storage/ 持久化 + hash 存档存在的核心理由。
    #
    # 重建触发点（三处）：
    #   ① 启动 app.py lifespan → rebuild_index() 首次建
    #   ② retrieve 前此处 _maybe_rebuild → 检测到变了才建
    #   ③ files.py 保存 MEMORY.md → rebuild_index() 强制建（用户主动改）
    def _maybe_rebuild(self) -> None:
        """Rebuild index if MEMORY.md has changed."""

        # 当前 MEMORY.md 的 MD5
        current_hash = self._get_file_hash()  
        # 上次建索引时的 MD5
        stored_hash = self._get_stored_hash()  

        # 内容变了（且当前非空）
        if current_hash and current_hash != stored_hash:  
            # 重建 + 落盘 + 更新 hash
            self.rebuild_index()  

    # rebuild_index（真正的重建索引）：读 MEMORY.md → 文档切片 → 向量化 → 构建LlamaIndex向量索引 → 构建完成后把索引持久化保存到磁盘，同时更新MEMORY.md的MD5哈希标记
    # 启动时 app.py 调一次；files.py 保存 MEMORY.md 时调一次；retrieve 检测到变化时调一次。
    #
    # 切块参数释义（SentenceSplitter chunk_size=256 overlap=32）：
    #   chunk_size=256：每块最多 256 字符（按句子边界对齐，不在句中硬切）
    #   chunk_overlap=32：相邻块重叠 32 字符——防语义在块边界断裂（如一句话被切成两半）
    #
    # 持久化内容：storage/memory_index/ 下存索引数据 + .memory_hash 存当前 MD5。
    # 下次 _load_index 直接复用，免 Embedding 调用。
    def rebuild_index(self) -> None:
        """Read MEMORY.md, split into chunks, build vector index, persist."""

        # 判断：如果 memory/MEMORY.md 文件根本不存在
        if not self._memory_path.exists(): 
            print("⚠️ memory/MEMORY.md not found, skipping index build") 
            # 索引置空，后续检索直接不可用
            self._index = None
            # 不建立索引，直接返回
            return  

        try:  
            # LlamaIndex 相关 import 放函数内：没装全时只在调用时报错
            from llama_index.core import (  # LlamaIndex Core
                Document,  # 文档对象
                StorageContext,  # 持久化上下文
                VectorStoreIndex,  # 向量索引
            )
            from llama_index.core.node_parser import SentenceSplitter  # 句子分块器
            from llama_index.core.settings import Settings  # 全局设置
            from llama_index.embeddings.openai import OpenAIEmbedding  # Embedding

            # 配 Embedding（与 search_knowledge_tool 同款配置，走 OpenAI 兼容接口）
            Settings.embed_model = OpenAIEmbedding(  # 设全局 Embedding
                model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),  # 向量模型
                api_key=os.getenv("DASHSCOPE_API_KEY"),                        # Embedding 服务密钥
                api_base=os.getenv(                                            # 服务地址（OpenAI 兼容）
                    "DASHSCOPE_BASE_URL", "https://ai.devtool.tech/proxy/v1"
                ),
            )

            # 读取MEMORY.md全部文本内容，utf-8编码
            content = self._memory_path.read_text(encoding="utf-8")

            # 判断：文件里面全是空白字符（空文件），不需要构建索引
            if not content.strip(): 
                self._index = None  # 索引置空
                return  # 没内容不建

            # 把原始文本包装成 LlamaIndex 的 Document 对象，带 source 元数据
            doc = Document(text=content, metadata={"source": "MEMORY.md"})

            # 分句切片器：SentenceSplitter 按句子切割文本
            splitter = SentenceSplitter(chunk_size=256, chunk_overlap=32)

            # 对文档进行切分，得到多个Node（切片块，RAG最小检索单元）
            nodes = splitter.get_nodes_from_documents([doc])

            # 确保 storage 目录存在
            self._storage_dir.mkdir(parents=True, exist_ok=True)  

            # 使用切分好的nodes，在内存构建向量索引
            index = VectorStoreIndex(nodes)

            # persist：持久化存储，把向量、文本、节点信息保存到storage/memory_index文件夹
            # 理论上后续可以从磁盘加载回来，不用重新embedding
            index.storage_context.persist(persist_dir=str(self._storage_dir)) 

            # 将新建好的索引实例，保存到成员变量self._index，内存缓存，后续检索直接使用
            self._index = index  # 缓存索引实例

            # 保存当前MEMORY.md的MD5哈希到.hash文件，标记本次构建对应的文件版本
            self._save_hash(self._get_file_hash()) 

            # 控制台打印日志，输出一共切出来多少个文本块
            print(f"🔄 Memory index rebuilt ({len(nodes)} chunks)")  # 重建日志

        # 捕获导入异常：缺少llamaindex、openai等依赖包
        except ImportError as e:
            print(f"⚠️ LlamaIndex not fully installed: {e}")  # 警告
            self._index = None  # 降级：索引置空，retrieve 返回空

        # 兜底捕获所有其他异常：网络失败、embedding接口报错、文件读取失败等
        except Exception as e: 
            print(f"⚠️ Memory index build error: {e}")  # 警告
            self._index = None  # 降级

    # _load_index（加载索引）：优先复用内存缓存的 _index，否则从磁盘 storage/memory_index 里已经保存好的向量索引加载到内存 self._index
    #
    # 两级缓存：
    #   L1 内存：self._index（实例存活期内复用，同一进程多次 retrieve 零加载成本）
    #   L2 磁盘：storage/memory_index/（进程重启后从这加载，免重建）
    # 命中 L1 直接返回；L1 miss 命中 L2 加载并回填 L1；都 miss 返回 None（retrieve 返回空）。
    #
    # 为什么加载也要配 Embedding：
    #   retrieve 阶段要把 query 向量化才能做相似度检索，所以加载索引时必须先配好 Embedding 模型。
    def _load_index(self) -> Any:
        """Load persisted index from storage."""

        # 如果内存里面已经存在索引实例，直接返回，不用重复加载（缓存机制）
        if self._index is not None:
            return self._index

        # 判断：索引存储目录不存在，或者目录是空的，没有索引文件 → 直接返回None
        if not self._storage_dir.exists() or not any(self._storage_dir.iterdir()):
            return None

        try:  
            # 从 storage 加载（需配 Embedding，因为检索时要向量化 query）
            from llama_index.core import StorageContext, load_index_from_storage  # 加载组件
            from llama_index.core.settings import Settings  # 全局设置
            from llama_index.embeddings.openai import OpenAIEmbedding  # Embedding

            # 配 Embedding（检索 query 时要用）
            Settings.embed_model = OpenAIEmbedding(  # 设全局 Embedding
                model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),  # 向量模型
                api_key=os.getenv("DASHSCOPE_API_KEY"),                        # Embedding 服务密钥
                api_base=os.getenv(                                            # 服务地址（OpenAI 兼容）
                    "DASHSCOPE_BASE_URL", "https://ai.devtool.tech/proxy/v1"
                ),
            )

            # StorageContext：LlamaIndex用来管理磁盘持久化资源的对象
            # from_defaults：从指定的persist_dir目录读取索引文件
            storage_context = StorageContext.from_defaults(  # 从 storage 建上下文
                persist_dir=str(self._storage_dir)
            )

            # 从磁盘加载索引，存入self._index，缓存到内存
            self._index = load_index_from_storage(storage_context)

            # 返回索引
            return self._index  

        # 加载索引失败（索引文件损坏、版本不兼容、embedding配置不一致）
        except Exception as e:
            print(f"⚠️ Failed to load memory index: {e}")
            return None

    # retrieve（检索记忆）：是 MemoryIndexer 对外暴露的检索入口，上层 RAG 调用这个方法，根据用户问题去 MEMORY.md 里面检索长期记忆片段
    # 三步：① _maybe_rebuild 检测变更 ② _load_index 加载索引 ③ as_retriever 检索。
    #
    # 返回值形态（agent.py 拿到后会包装成 [记忆检索结果] 注入对话历史）：
    #   [{"text": "片段文本", "score": "0.8421", "source": "MEMORY.md"}, ...]
    #   - text：记忆片段原文（切块时定长 256 字符）
    #   - score：相似度分数（4 位小数）；get_score 为空时显示 "N/A"
    #   - source：来源标签，建索引时塞的 metadata，恒为 "MEMORY.md"
    #
    # 降级链路（任一失败都返回空列表，Agent 照常工作）：
    #   - MEMORY.md 不存在 / 内容空 → _index=None → 返回 []
    #   - LlamaIndex 没装全 → _index=None → 返回 []
    #   - Embedding 调用失败 → except → 返回 []
    #   - 持久化索引损坏 → _load_index except → 返回 []（下次 _maybe_rebuild 会重建）
    def retrieve(
        self, query: str, top_k: int = 3
    ) -> list[dict[str, Any]]:
        """Retrieve relevant memory chunks for a query."""

        # 先检测 MEMORY.md 是否变了，变了才重建索引
        self._maybe_rebuild()  

        # 加载索引到内存（优先读缓存，没有就从磁盘加载）
        index = self._load_index()

        if index is None:  # 索引仍为空（记忆空 / LlamaIndex 没装）
            return []  # 返回空列表，Agent 照常工作（RAG 退化）

        try:  # 检索阶段

            # 把索引转为检索器
            retriever = index.as_retriever(similarity_top_k=top_k)

            # 执行检索，传入用户query，返回NodeWithScore列表
            nodes = retriever.retrieve(query) 

            results: list[dict[str, Any]] = []  # 收集结果

            # 遍历检索结果，把Node对象转成普通字典，方便上层代码使用
            for node in nodes:
                results.append({  # 每个结果含 text/score/source
                    # 片段文本
                    "text": node.get_text(),  
                    # 相似度分数，保留4位小数；没有分数就填N/A
                    "score": f"{node.get_score():.4f}" if node.get_score() else "N/A",
                    # 来源（建索引时塞的元数据）
                    "source": node.metadata.get("source", "MEMORY.md"),  
                })

            # 返回检索结果列表
            return results  

        # 检索过程异常兜底，返回空列表，不崩溃Agent
        except Exception as e:
            print(f"⚠️ Memory retrieval error: {e}")  # 警告
            return []  # 返回空列表，Agent 照常工作


# 模块级私有全局变量，保存MemoryIndexer单例实例，get_memory_indexer 首次调用时创建，后续复用。
# FastAPI 多 worker 时每个进程各持一份（本项目本地单进程无碍）。
#
# 为什么用单例：
#   - 避免重复建索引（Embedding API 调用花钱）
#   - 内存索引实例 _index 跨调用复用（同一会话多次 retrieve 不重新加载）
#   - hash 比对状态在实例内累积（_maybe_rebuild 据此判断）
# 多 worker 注意：状态在进程内存 + 文件系统，多进程各自持有一份——并发写 storage/ 有覆盖风险，
# 本项目单进程本地场景无碍；若上多进程需加文件锁或迁移数据库。
# 类型注解：要么是MemoryIndexer对象，要么是None（还没创建）
_instance: MemoryIndexer | None = None  # 单例实例

# get_memory_indexer（拿单例）：首次调用创建 MemoryIndexer，后续直接返回。
# app.py lifespan / agent.py astream / files.py 保存触发 都走这里拿实例。
def get_memory_indexer(base_dir: Path) -> MemoryIndexer:
    """Get or create the singleton MemoryIndexer."""

    # global 声明：告诉python，这个函数里面的 _instance 是模块顶部的全局变量，不是函数内部局部变量
    global _instance  # 声明用模块级单例

    # 首次调用
    if _instance is None:  
        _instance = MemoryIndexer(base_dir)  # 新建MemoryIndexer对象，赋值给全局_instance

    # 无论新建还是已有，返回这个唯一实例
    return _instance