# ============================================================
# tools/__init__.py  Agent 的 "工具箱总装车间" 。
# __init__.py 的作用：标记当前文件夹是一个 Python 包（package），并且可以对外暴露接口、封装工厂函数
# 职责单一：get_all_tools(base_dir) 一次性装配 5 大核心工具，返回给 AgentManager 注册进 create_agent。
# 顺序即工具在 LLM 工具列表中的展示顺序（terminal → python_repl → fetch_url → read_file → search_knowledge）。
# 安全设计：terminal / read_file / search_knowledge 都接收 base_dir 做沙箱锚点（cwd 锁定 / 路径越界检查 / 索引根）；
#           python_repl / fetch_url 无需 base_dir（REPL 自带环境、fetch 只认 URL）。
# 谁在用：graph/agent.py AgentManager.initialize 调一次，结果存为 self._tools 全程复用。
# ============================================================
"""Core Tools factory — returns all 5 tools for the Agent."""

from pathlib import Path                              # base_dir 类型，沙箱锚点
from typing import List                               # List[BaseTool] 返回类型注解（PRD 强制 Type Hinting）
from langchain_core.tools import BaseTool             # 所有工具的基类（name/description/args_schema/_run 契约）
# 5 个工具的工厂函数：每个 create_xxx_tool 返回一个配好的 BaseTool 实例
from .terminal_tool import create_terminal_tool           # 沙箱终端（黑名单 + cwd 锁定）
from .python_repl_tool import create_python_repl_tool     # Python 解释器
from .fetch_url_tool import create_fetch_url_tool         # 网页抓取 → Markdown
from .read_file_tool import create_read_file_tool         # 沙箱文件读取（技能机制依赖）
from .search_knowledge_tool import create_search_knowledge_tool  # knowledge/ 知识库检索

# get_all_tools（工具总装）：装配 5 大工具返回。base_dir 传给需要沙箱锚点的三个工具。
# 作用：外部代码不用逐个导入 5 个工具、逐个实例化，只需要调用这一个函数，一次性拿到全部 5 个工具实例
def get_all_tools(base_dir: Path) -> List[BaseTool]:
    """Create and return all 5 core tools, sandboxed to base_dir."""
    return [  # 列表顺序 = LLM 工具列表展示顺序，无业务含义但保持稳定便于 Agent 记忆
        create_terminal_tool(base_dir),              # 终端：cwd 锁定到 base_dir + 12 条黑名单
        create_python_repl_tool(),                   # Python REPL：无需 base_dir，自带独立环境
        create_fetch_url_tool(),                     # 网页抓取：无需 base_dir，只认 URL
        create_read_file_tool(base_dir),             # 文件读取：锁定 base_dir 为根，防越界
        create_search_knowledge_tool(base_dir),      # 知识库检索：knowledge/ 在 base_dir 下
    ]

# ────────────────────────────────────────────────────────────
# 装配顺序固定：Agent 在工具列表里看到的顺序即此处的列表顺序，保持稳定便于 Agent 记忆。
# 新增工具：在 tools/ 下建 xxx_tool.py 写 create_xxx_tool()，到上面 return 列表加一行即可。
# ────────────────────────────────────────────────────────────

# ❌ 不使用 init.py 工厂函数（啰嗦写法，项目外部调用）
    # 要写一堆import，5个工具就要导入5次
    # from tools.terminal_tool import create_terminal_tool
    # from tools.python_repl_tool import create_python_repl_tool
    # from tools.fetch_url_tool import create_fetch_url_tool
    # from tools.read_file_tool import create_read_file_tool
    # from tools.search_knowledge_tool import create_search_knowledge_tool

    # base_dir = Path("./")
    # tools = [
    #     create_terminal_tool(base_dir),
    #     create_python_repl_tool(),
    #     create_fetch_url_tool(),
    #     create_read_file_tool(base_dir),
    #     create_search_knowledge_tool(base_dir),
    # ]

# ✅ 使用 init.py 聚合后的简洁写法（项目推荐）
    # 只需要一行导入
    # from tools import get_all_tools

    # base_dir = Path("./")
    # tools = get_all_tools(base_dir)
    # # tools 直接拿到5个BaseTool实例列表，直接丢给 llm.bind_tools(tools)

# 这就是写在__init__.py最大好处：对外简化 API，内部细节全部隐藏。 外部主程序不用关心内部有多少个工具文件、每个工具怎么创建。想新增 / 删除工具，只改 tools/init.py 里面的 get_all_tools 列表，外部代码完全不用动

# 项目级视角：为什么 mini OpenClaw 要这么设计？
# 1. 解耦：每个工具独立一个文件，单一职责；
# 2. 统一收口：所有工具注册、实例化全部在`get_all_tools`这一处管理；
# 3. 方便开关工具：想临时禁用某个工具，直接注释掉列表里一行，主代码不用修改；
# 4. 统一传入 base_dir：文件类工具（terminal、read_file、知识库）都需要项目根目录，在这里统一传入