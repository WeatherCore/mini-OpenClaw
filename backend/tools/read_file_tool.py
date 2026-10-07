# ============================================================
# read_file_tool.py — Agent 的 本地文件读取工具，让 Agent 可以读取项目内的 md、配置文件。
# 职责：让 Agent 在沙箱内读取项目文件——最关键用途是读 SKILL.md 说明书学技能（见 AGENTS.md 协议）。
# 安全设计：path 归一化后 resolve()，结果必须仍在 root_dir 内，否则判越界拒绝。
#           这样 Agent 即便传 "../../etc/passwd" 也会被拦在项目根之外。
# 输出约定：超 10000 字符截断 + 加 ...[truncated]；错误统一以 ❌ 开头返回给 Agent（它能读懂失败原因自行调整）。
# 与 files.py 的关系：files.py 是前端 HTTP 层的文件读写（白名单目录），本工具是 Agent 内部读文件（项目根沙箱）。
#
# 技能调用协议的执行体（PRD §三 2.2）：
#   Agent 决定用某技能 → 第一步永远是 read_file("skills/<name>/SKILL.md")
#   → 读懂说明书后用 fetch_url/python_repl 等核心工具组合执行。
#   本工具就是这条链路的"读说明书"动作的落点。
#
# 沙箱越界检查示例：
#   Agent 传 "skills/get_weather/SKILL.md" → resolve 后在 root 内 → 放行
#   Agent 传 "../../etc/passwd"            → resolve 后跑出 root → ❌ Access denied
#   Agent 传 "nonexistent.md"              → resolve 后在 root 内但不存在 → ❌ File not found
# ============================================================
"""ReadFileTool — sandboxed file reading within project directory."""
# 一句话：只能读取项目目录内部的文件，禁止读取项目外面的文件

from pathlib import Path                               # 路径归一化与越界检查的核心
from typing import Type                                 # Type[BaseModel] 类型注解

from langchain_core.tools import BaseTool              # 工具基类
from pydantic import BaseModel, Field                  # 入参 schema


# ReadFileInput（入参 schema）：约束 Agent 传 file_path 字段，且是相对项目根的相对路径。
class ReadFileInput(BaseModel):
    
    # 待读文件的相对路径（相对项目根）
    file_path: str = Field(  
        description="Relative path of the file to read (relative to project root)"
    )

# SandboxedReadFileTool（沙箱读文件工具）：BaseTool 子类，_run 是 Agent 调用入口。
# 安全模型：归一化 → resolve → 起始串检查。resolve 会解析掉 .. 与符号链接，
# 之后比绝对路径是否仍以 root 开头——这是防路径穿越的标准做法（比单纯拦 .. 子串更可靠）。
class SandboxedReadFileTool(BaseTool):
    name: str = "read_file"  # 工具名（Agent 调用时用，对齐 PRD）
    description: str = (  # 教 Agent 何时用、给了一个具体例子
        "Read the content of a local file. Path is relative to the project root. "
        "Use this to read SKILL.md files, MEMORY.md, configuration files, etc. "
        "Example: read_file('skills/get_weather/SKILL.md')"  # few-shot 例子：直接告诉 Agent 怎么读技能
    )
    args_schema: Type[BaseModel] = ReadFileInput  # 入参 schema

    # 实例属性：保存项目根目录的绝对路径，用来做沙箱边界校验
    # 工厂函数`create_read_file_tool(base_dir: Path)`在创建工具的时候，会把项目根目录传进去
    root_dir: str = ""  

    # _run（执行读文件）：归一化路径 → 越界检查 → 存在性检查 → 读内容 → 截断 → 返回。
    # 5 道防线顺序不可乱：越界检查必须在存在性检查之前（否则可用报错差异探测文件是否存在）。
    def _run(self, file_path: str) -> str:

        try:
            # 将保存的根目录字符串转为Path对象，方便后续路径运算
            root = Path(self.root_dir)  # 沙箱根
            # 路径标准化：统一把windows反斜杠转为正斜杠；清除路径开头多余的./，统一格式
            normalized = file_path.replace("\\", "/").lstrip("./") 

            # 拼接根目录和传入相对路径，resolve解析为绝对路径，自动处理../跳转
            full_path = (root / normalized).resolve() 

            # 沙箱核心防护：判断解析后的绝对路径是否在项目根目录内，防御路径穿越攻击
            if not str(full_path).startswith(str(root.resolve())):
                # 路径逃逸，直接返回拒绝访问提示文本给大模型
                return f"❌ Access denied: path escapes project root"

            # 判断：目标文件不存在 → 返回拒绝访问提示文本给大模型
            if not full_path.exists():
                return f"❌ File not found: {file_path}"

            # 判断：是目录不是文件：告诉 Agent 路径指错类型
            if not full_path.is_file():  
                return f"❌ Not a file: {file_path}"

            # 以utf-8编码读取文件全部文本内容
            content = full_path.read_text(encoding="utf-8")

            # 超 10000 字符截断（比 fetch 宽松，文件内容通常更有价值）
            if len(content) > 10000:  
                 # 截断 + 标记
                content = content[:10000] + "\n...[truncated]" 

            # 返回文件内容给 Agent
            return content  
        
        except Exception as e:  # 其他异常（权限不足等）：转 ❌ 文本，不抛异常打断 Agent
            return f"❌ Error reading file: {str(e)}"


# create_read_file_tool（建读文件工具）：工厂函数，注入 root_dir 沙箱锚点。
def create_read_file_tool(base_dir: Path) -> SandboxedReadFileTool:
    # 接收项目根目录，实例化工具并注入根目录参数，返回工具实例
    return SandboxedReadFileTool(root_dir=str(base_dir))
