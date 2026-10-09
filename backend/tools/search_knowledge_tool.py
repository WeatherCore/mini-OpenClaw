# ============================================================
# search_knowledge_tool.py — Agent 的知识库检索工具（RAG 检索工具）
# 基于 LlamaIndex 实现 BM25 关键词 + 向量语义 混合检索（Hybrid Retrieval，PRD 要求），RRF 融合排序。
# 职责：读 backend/knowledge/ 下的 PDF/MD/TXT，建向量索引并持久化；检索时 BM25 与向量双路召回，
#       用 QueryFusionRetriever（倒数排名融合 RRF）合并排序，返回最相关的知识片段。
#
# 混合检索原理（为什么 BM25 + 向量缺一不可）：
#   - BM25 关键词路：字面精确匹配强——专有名词、代码标识符、型号等"词对词"命中，
#     向量对这类生僻精确词经常召回一堆语义相近但不命中的片段；
#   - 向量语义路：跨表述理解强——"怎么让电脑重新开始"能召回写着"重启计算机"的片段，
#     BM25 对这种同义改写束手无策；
#   - 两路各取 top-4，RRF 融合后取 top-3：兼顾"字面精确"与"语义泛化"。
#
# 为什么返回"片段"而不是"合成答案"（与旧实现的差异）：
#   - 旧实现 as_query_engine = 检索 + LLM 合成；但本项目只给 LlamaIndex 配了 Embedding、没配 LLM，
#     合成会去调默认 OpenAI 接口（无 key）而报错；
#   - 且外层 Agent 本身就是 LLM，拿到带来源的原始片段自己就能合成，还省一次 LLM 调用；
#   - 故改为纯检索：返回带来源标注的 top-3 片段，description 里 "Returns the most relevant
#     passages" 至此名副其实。
#
# 索引生命周期（三段式）：
#   首次 _run → _build_index() → knowledge/ 有文档？
#     ├ 否 → 返回 None（_run 返回"知识库为空"提示）
#     └ 是 → storage/ 有持久化索引？
#         ├ 是 → load_index_from_storage 复用（免 Embedding 调用）
#         └ 否 → SimpleDirectoryReader 读文档 → VectorStoreIndex.from_documents → persist 到 storage/
#   首个 _run 建好索引后 → _build_hybrid_retriever() 组装混合检索器并缓存，后续 _run 直接复用。
#   （BM25 是内存倒排索引，基于已加载的 docstore 现算，构建毫秒级，不值得多一份落盘）
#
# BM25 中文分词（本项目的一处关键定制，正则见 CJK_AWARE_TOKEN_PATTERN）：
#   - bm25s 默认正则 (?u)\b\w\w+\b 会把"整段连续汉字"当成一个词（"什么是机器学习"是一个 token），
#     查询和文档几乎不可能切出相同的 token → BM25 对中文完全失效；
#   - 定制正则改为 CJK 逐字 + 英文/数字按词，零依赖解决中文匹配；
#   - 若要进一步提升可引 jieba 做词级分词（需在 requirements.txt 加依赖并改写正则），
#     新版 BM25Retriever 只收 token_pattern 正则，旧的 tokenizer 回调参数已被废弃并忽略。
#
# 降级策略（工具不能拖垮 Agent，层层兜底）：
#   llama-index-retrievers-bm25 未装 / BM25 构建失败 → 退化为纯向量检索并打印警告；
#   LlamaIndex 没装全 / 建索引失败 → _run 返回"知识库为空"提示。
#
# 返回形态（Agent 拿到的）：
#   - 命中：带 [序号] (score, source) 标注的片段列表（≤5000 字符，超则截断）
#   - 空库：📭 Knowledge base is empty. Add documents to backend/knowledge/ to enable search.
#   - 有库无命中：🔍 No relevant passages found ...
#   - 异常：❌ Search error: <原因>
# ============================================================
"""SearchKnowledgeBaseTool — LlamaIndex hybrid search (BM25 + Vector, RRF fusion)."""
# 知识库检索工具，基于LlamaIndex实现混合检索：BM25关键词检索 + 向量语义检索，使用RRF倒数排名融合算法合并多路召回结果

import os                                              # 读环境变量配置 Embedding
from pathlib import Path                               # 路径拼接
from typing import Type, Optional, List, TYPE_CHECKING  # 类型注解
from langchain_core.tools import BaseTool              # 工具基类
from pydantic import BaseModel, Field                  # 入参 schema

# NodeWithScore 属于 llama_index.core —— 按本项目"延迟导入 LlamaIndex"原则，
if TYPE_CHECKING:  
    from llama_index.core.schema import NodeWithScore   # 检索结果项（内容片段 + 融合分数）

# ── 检索参数（集中定义便于调参）────────────────────────────────────
# 每路召回条数：BM25 与向量各自取 top-4，给融合层留足候选
# （两路结果常大量重叠，4+4 经 RRF 去重合并后取 3，比任何单路 top-3 质量更高）
PER_RETRIEVER_TOP_K = 4
# 融合后最终返回条数：与旧实现 similarity_top_k=3 对齐，控制注入 Agent 上下文的长度
FUSED_TOP_K = 3

# BM25 分词正则（关键定制，原理见文件头"BM25 中文分词"）：
#   [\u4e00-\u9fff]     — CJK 统一汉字逐字切分（单字粒度，无对齐问题，零依赖）
#   [A-Za-z0-9_]{2,}   — 英文/数字/下划线按词切分（≥2 字符，顺带滤掉 a/I 等高频无义词）
CJK_AWARE_TOKEN_PATTERN = r"[\u4e00-\u9fff]|[A-Za-z0-9_]{2,}"

# 返回给 Agent 的文本长度上限（字符），超出截断——防止大片段撑爆 Agent 上下文窗口
MAX_RESULT_CHARS = 5000

# SearchKnowledgeInput（入参 schema）：约束 Agent 传 query 字段。
class SearchKnowledgeInput(BaseModel):
    # 待检索的问题，必填
    query: str = Field(description="The search query to find relevant knowledge")

# SearchKnowledgeBaseTool（知识库检索工具）：BaseTool 子类。
# _index / _retriever 均懒加载：首次 _run 才建索引、组装检索器，
# 避免后端启动时无谓建索引（用户可能从不用这个工具）。
class SearchKnowledgeBaseTool(BaseTool):
    name: str = "search_knowledge_base"
    description: str = (
        "Search the local knowledge base using hybrid retrieval "
        "(BM25 keyword search + vector semantic search, fused with Reciprocal Rank Fusion). "
        "Use this when the user asks about specific knowledge or documents. "
        "Returns the most relevant passages with their sources from the knowledge base."
    )
    args_schema: Type[BaseModel] = SearchKnowledgeInput

    # 项目根目录路径字符串，knowledge/ 与 storage/ 都在此下
    base_dir: str = ""
    # LlamaIndex 向量索引实例（懒加载：首次 _run 才建）
    _index: Optional[object] = None
    # 混合检索器实例（懒加载：索引建好后组装一次，后续 _run 复用）
    _retriever: Optional[object] = None

    # Pydantic配置项：允许模型内存在任意自定义类型对象（LlamaIndex的Index不是原生pydantic类型，不加这行会报错）
    class Config:
        arbitrary_types_allowed = True  # 放开类型校验，让 _index/_retriever 能存 LlamaIndex 对象

    # _build_index（建索引）：读 knowledge/ → 配 Embedding → 优先复用 storage/ → 否则新建并持久化。
    # 任何失败都返回 None（_run 会据此返回"知识库为空"提示），不让索引层拖垮 Agent。
    # 三段式：① 知识源检查 ② 持久化复用 ③ 新建并落盘。
    #
    # 为什么三段式都要 try：
    #   - 持久化复用失败（索引损坏/版本不兼容）要能降级到新建，不能一个坏 storage 就让工具废掉；
    #   - 新建失败（Embedding API 不通/没有文档）要返回 None 让 _run 给出"知识库为空"，不抛异常打断 Agent。
    #
    # Embedding 调用时机：只在"新建"分支才真正调 Embedding API（VectorStoreIndex.from_documents 内部）；
    # 持久化复用分支零 Embedding 建索引调用——这是省钱的关键，也是 storage/ 持久化存在的理由。
    def _build_index(self):
        """Build or load LlamaIndex index from knowledge/ directory."""

        # 拼接知识库文档目录：项目根目录/knowledge，存放原始md/txt等文档
        knowledge_dir = Path(self.base_dir) / "knowledge"
        # 拼接索引存储目录：项目根目录/storage，存放向量索引、embedding持久化文件
        storage_dir = Path(self.base_dir) / "storage"

        # ── 第 1 段：知识源检查 ──
        # 知识库文件夹 knowledge/ 不存在或空 → 没东西可索引，直接返回 None
        if not knowledge_dir.exists() or not any(knowledge_dir.iterdir()):
            return None

        try:
            # 延迟导入！！LlamaIndex 相关 import 放函数内：没装全时只在调用时报错，不影响整个后端启动
            from llama_index.core import (      # LlamaIndex Core 主组件
                SimpleDirectoryReader,          # 读 knowledge/ 下文档
                StorageContext,                 # 索引持久化上下文
                VectorStoreIndex,               # 向量索引
                load_index_from_storage,        # 从持久化加载索引
            )
            from llama_index.core.settings import Settings              # LlamaIndex 全局设置
            from llama_index.embeddings.openai import OpenAIEmbedding   # OpenAI 兼容 Embedding

            # 配置全局 Embedding 模型：走 OpenAI 兼容接口（默认代理地址，可被 .env 覆盖）
            Settings.embed_model = OpenAIEmbedding(
                model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),  # 向量模型
                api_key=os.getenv("DASHSCOPE_API_KEY"),                        # Embedding 服务密钥
                api_base=os.getenv(                                            # 服务地址（OpenAI 兼容）
                    "DASHSCOPE_BASE_URL", "https://ai.devtool.tech/proxy/v1"
                ),
            )

            # ── 第 2 段：优先复用持久化索引 ──
            # 判断：如果storage索引目录已经存在且里面有索引文件 → 优先加载旧索引，不用重新向量化文档（节省token和时间）
            if storage_dir.exists() and any(storage_dir.iterdir()):

                try:
                    # 从storage文件夹加载持久化存储上下文
                    storage_context = StorageContext.from_defaults(  # 从 storage/ 建上下文
                        persist_dir=str(storage_dir)
                    )

                    # 加载已存在的向量索引，直接返回索引对象
                    return load_index_from_storage(storage_context)

                # 加载旧索引失败（索引损坏、版本不兼容），捕获异常，进入新建索引逻辑
                except Exception:
                    pass

            # ── 第 3 段：新建索引 ──
            # 读取 knowledge/ 全部文档 → 向量化 → 持久化（下次复用免 Embedding 调用）
            documents = SimpleDirectoryReader(
                # recursive=True 递归读取子文件夹里所有文件（PDF/MD/TXT）
                str(knowledge_dir), recursive=True
            ).load_data()

            # 读到空（如目录全是空文件），返回None
            if not documents:
                return None

            # 构建向量索引（此处才真正调 Embedding API）。
            # 注意：向量索引只服务"语义"那一路；BM25"关键词"路不在此建——它在检索期
            # 基于已加载索引的 docstore 现算内存倒排（见 _build_hybrid_retriever），无需持久化。
            index = VectorStoreIndex.from_documents(documents)

            # 确保 storage/ 存在
            storage_dir.mkdir(parents=True, exist_ok=True)
            # 将构建好的索引持久化保存到storage目录，下次启动直接加载，不需要重复embedding
            index.storage_context.persist(persist_dir=str(storage_dir))

            # 返回新建索引
            return index

        # LlamaIndex 没装全：打印警告返回 None，工具降级为"知识库为空"
        except ImportError as e:
            print(f"⚠️ LlamaIndex not fully installed: {e}")
            return None
        # 其他建索引异常（Embedding 调用失败等）
        except Exception as e:
            print(f"⚠️ Index build error: {e}")
            return None

    # _build_hybrid_retriever（组装混合检索器）：向量检索器 + BM25 检索器 → QueryFusionRetriever（RRF 融合）。
    # 在 _index 建好后调用一次并缓存；任何一步失败都降级为纯向量检索器（不能因 BM25 挂掉而废掉工具）。
    #
    # num_queries=1 的含义：QueryFusionRetriever 默认会先让 LLM 把原查询改写/扩展成多路查询
    # （num_queries=4）——那需要给 LlamaIndex 配 LLM，且 PRD 的 Hybrid 指"双路召回+融合"，
    # 不含查询扩展。设 1 = 原查询同时喂给两路检索器，融合器零 LLM 调用。
    #
    # use_async=False 的含义：融合检索器默认走异步（内部 asyncio.run），而本工具被 LangGraph/
    # FastAPI 的同步上下文调用，同线程里可能已有事件循环，asyncio.run 会直接抛错；
    # 关掉异步走纯同步 _retrieve 最稳。
    def _build_hybrid_retriever(self):
        """Combine vector + BM25 retrievers with RRF fusion; fall back to vector-only on failure."""

        try:
            # 延迟导入：BM25 检索器在独立子包（requirements.txt: llama-index-retrievers-bm25），
            # 没装时只在调用时报错，不影响后端启动，走降级分支
            from llama_index.retrievers.bm25 import BM25Retriever                  # BM25 关键词检索器（bm25s 内核）
            from llama_index.core.retrievers import QueryFusionRetriever           # 多路融合检索器
            from llama_index.core.retrievers.fusion_retriever import FUSION_MODES  # 融合策略枚举
            from llama_index.core.llms.mock import MockLLM                         # core 内置假 LLM（见下）

            # 向量语义路：由索引直接产出检索器（复用全局 Settings.embed_model 做 query 向量化），
            # 召回与语义最相关的 top-4 片段——负责"同义改写也能命中"
            vector_retriever = self._index.as_retriever(
                similarity_top_k=PER_RETRIEVER_TOP_K
            )

            # BM25 关键词路：直接吃已加载索引的 docstore（所有切块都在里面，零重复加载/零重复分块），
            # token_pattern 换成中文逐字正则（默认正则对中文失效，见文件头）；
            # 构建是内存倒排索引，毫秒级，不值得持久化。
            # 词干提取保持默认（英文词干器对中文字符原样返回、对英文仍有 running→run 的收益，已验证）。
            bm25_retriever = BM25Retriever.from_defaults(
                docstore=self._index.docstore,
                similarity_top_k=PER_RETRIEVER_TOP_K,
                token_pattern=CJK_AWARE_TOKEN_PATTERN,
            )

            # 融合：RECIPROCAL_RANK = RRF（倒数排名融合）——片段在 BM25 排第 r1、向量排第 r2，
            # 融合得分 = 1/(60+r1) + 1/(60+r2)。两路分数尺度不同（BM25 相关性 vs 余弦相似度）没关系，
            # RRF 只看排名不看分数值，这正是选它而不是加权求和的原因（加权需调归一化超参，RRF 免调）。
            # [注意] 0.14 起默认融合模式改成了 SIMPLE（简单按原分数重排），必须显式传 RRF。
            return QueryFusionRetriever(
                [vector_retriever, bm25_retriever],
                similarity_top_k=FUSED_TOP_K,
                mode=FUSION_MODES.RECIPROCAL_RANK,
                num_queries=1,       # num_queries=1 关闭查询改写
                use_async=False,
            )

        # BM25 子包没装 / 融合器导入失败：退化纯向量，混合检索少一路但工具仍可用
        except ImportError as e:
            print(f"⚠️ BM25 retriever unavailable, falling back to vector-only search: {e}")
            return self._index.as_retriever(similarity_top_k=FUSED_TOP_K)
        # BM25 构建失败（版本不兼容 / token_pattern 参数不被支持 / docstore 异常等）：同样退化
        except Exception as e:
            print(f"⚠️ Hybrid retriever build failed, falling back to vector-only search: {e}")
            return self._index.as_retriever(similarity_top_k=FUSED_TOP_K)

    # _format_passages（格式化检索结果）：
    # 静态工具方法：把融合召回后的Node结果，格式化为带序号、分数、来源、页码的可读文本，返回给LLM
    # 每个片段一行头：[序号] (score=融合得分, source=文件名[, page=页码]) + 正文换行 + 原文。
    # Agent 拿到原始片段之后，自己基于这些材料推理回答，方便做引用溯源，降低幻觉
    @staticmethod
    def _format_passages(nodes: List["NodeWithScore"]) -> str:
        """Format fused retrieval results into numbered, source-attributed passages."""

        # 存放每一条格式化后的检索片段
        parts = []

        # 遍历召回结果，序号从1开始计数
        for i, node_with_score in enumerate(nodes, start=1): 

            # 真正的文本块（chunk节点，就是我们RAG里存的文档片段）
            node = node_with_score.node
            # 取出节点元数据：SimpleDirectoryReader 写入的文件信息，兜底为空字典，防止metadata为None时报错
            meta = node.metadata or {}

            # 逐级获取文档来源名称：file_name（常见）> file_path 取文件名 > source（自定义元数据）> unknown
            source = (
                meta.get("file_name")
                or (Path(meta["file_path"]).name if meta.get("file_path") else None)
                or meta.get("source")
                or "unknown"
            )

            # 获取页码元数据（PDF文档常用），其他文档类型为空
            page = meta.get("page_label") 
            # 如果存在页码，拼接页码字符串，没有则为空
            location = f", page={page}" if page else "" 

            # 获取检索相似度分数，空值兜底为0.0
            score = node_with_score.score if node_with_score.score is not None else 0.0
            # 构造头部信息：序号、保留4位小数的分数、来源、页码
            header = f"[{i}] (score={score:.4f}, source={source}{location})"

            # 获取文档片段原文，去除首尾空白
            text = node.get_content().strip()

            parts.append(f"{header}\n{text}")

        # 所有片段用两个换行分隔，拼接成完整字符串返回    
        return "\n\n".join(parts) 

    # _run（执行检索）：懒加载索引 → 懒组装混合检索器 → 双路召回 + RRF 融合 → 格式化 → 截断 → 返回。
    # 返回值四种形态（Agent 据前缀判断状态）：
    #   - 命中：[1] (score=..., source=...) 开头的片段列表（≤5000 字符，超则截断加 ...[truncated]）
    #   - 空库：📭 开头，提示往 knowledge/ 加文档
    #   - 有库但没命中：🔍 开头，提示换问法或补文档（与空库区分：此时索引是建好的）
    #   - 异常：❌ 开头，告诉 Agent 检索出错原因
    def _run(self, query: str) -> str:

        # 索引未建：首次调用时懒加载
        if self._index is None:  
            self._index = self._build_index() 

        # 索引构建失败/知识库为空，返回提示文本给大模型
        if self._index is None:
            # 提示 Agent 引导用户加文档到 knowledge/ 目录
            return "📭 Knowledge base is empty. Add documents to backend/knowledge/ to enable search."  

        try:  
            # 索引就绪后，懒加载构建混合检索器
            if self._retriever is None:
                self._retriever = self._build_hybrid_retriever()

            # 执行多路检索+RRF融合，拿到召回的文档节点列表
            retrieved = self._retriever.retrieve(query)

            # 召回结果为空，返回提示，告诉LLM没有找到相关片段，可以换query重试，或者引导用户加文档
            if not retrieved:  
                return "🔍 No relevant passages found in the knowledge base for this query. Try rephrasing, or add more documents to backend/knowledge/."

            # 调用格式化方法，把召回节点转为带来源、分数的文本
            result = self._format_passages(retrieved)

            # 超长截断（防撑爆 Agent 上下文窗口）
            if len(result) > MAX_RESULT_CHARS:  
                result = result[:MAX_RESULT_CHARS] + "\n...[truncated]"  # 截断 + 标记

            # 返回片段列表给 Agent（答案合成由 Agent 自己完成，省一次 LLM 调用）
            return result  

        # 检索异常（query 向量化失败 / 检索器内部错误等）
        except Exception as e:  
            return f"❌ Search error: {str(e)}"


# create_search_knowledge_tool（建检索工具）：工厂函数，注入 base_dir 让工具知道 knowledge/ 在哪。
def create_search_knowledge_tool(base_dir: Path) -> SearchKnowledgeBaseTool:
    # base_dir=项目根，knowledge/ 与 storage/ 都在此下
    return SearchKnowledgeBaseTool(base_dir=str(base_dir))  