# ============================================================
# python_repl_tool.py — Agent 的 代码执行器工具，是对 LangChain 官方实验包工具做一层包装
# 职责：包装 LangChain experimental 的 PythonREPLTool，改名为 python_repl 并配好说明（教 Agent 何时用、怎么用）。
# 这是 5 大工具里最薄的一个——不自定义执行逻辑，只做"出厂设定"（name + description）。
# Agent 用它做：逻辑计算、数据处理、跑脚本、解析 API 返回的 JSON 等任何"写代码比写自然语言快"的活。
# 注意：PythonREPLTool 在 langchain_experimental 包（非稳定 API），依赖需单独装好。
# ============================================================
"""Python REPL Tool — wraps LangChain experimental PythonREPLTool."""

from langchain_core.tools import BaseTool              # 工具基类（返回类型注解）
from langchain_experimental.tools import PythonREPLTool  # LangChain 自带 REPL 工具，自带独立 Python 交互环境

# create_python_repl_tool（建 Python 工具）：实例化原生 PythonREPLTool，改名 + 配 description。
# 返回类型注解`BaseTool`：返回的实例是 LangChain 标准工具对象，可以直接放进 Agent 的工具列表
# 改名 python_repl 是为了让 Agent 在工具调用里用这个名字（原生默认名是 python_repl_tool，与 PRD 约定的 python_repl 不一致）。
def create_python_repl_tool() -> BaseTool:

    # 实例化 LangChain 自带的 Python 代码执行工具
    # 原生自带基础能力：接收 Python 代码字符串，执行代码，捕获 stdout 输出、捕获异常栈信息
    tool = PythonREPLTool()

    # 写给大模型的工具的相关描述说明
    tool.name = "python_repl" 
    tool.description = (  
        # 多行字符串拼成完整说明：场景 + 输入规范 + 看输出的诀窍
        "Execute Python code in an interactive REPL environment. "
        "Use this for calculations, data processing, running scripts, "
        "and any task that benefits from programmatic execution. "
        "Input should be valid Python code. "
        "Use print() to see output."  # 关键提示：REPL 默认不回显表达式结果，必须 print() 才能看到
    )
    # 拆解里面的信息点：
    # 1. 能力：在交互式 REPL 环境执行 Python 代码
    # 2. 适用场景：数学计算、数据处理、脚本运行
    # 3. 入参要求：必须是合法 Python 代码
    # 4. 重要提示：要用 print () 输出结果
    # PythonREPL 只会捕获print()打印到标准输出的内容。如果代码只写变量计算，没有 print，工具返回是无输出/空字符串，大模型拿不到结果

    # 返回配置好的工具实例，上层代码拿到这个实例，和`fetch_url`一起放进 Agent 的工具列表
    return tool
