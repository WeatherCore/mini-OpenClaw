# ============================================================
# terminal_tool.py — Agent 的"沙箱 shell 终端工具"（5 大工具里安全要求最高的一个）。
# 职责：让 Agent 在受限环境执行 Shell 命令，三道防线防删库跑路：
#   ① 12 条高危命令黑名单（子串匹配，命中即拒）；
#   ② cwd=root_dir 锁定工作目录到项目根（Agent 出不去 backend/）；
#   ③ 30s 超时 + 5000 字符截断（防命令挂死、防输出爆 Token）。
# 设计取舍：黑名单是子串匹配，绕过手法存在（如变量拼接）——这是纵深防御的一层而非唯一防线，
#           真要安全应配容器/虚拟机沙箱；本项目本地单用户场景，黑名单 + cwd 锁够用。
# 输出约定：stderr 单独以 [stderr] 段拼接在 stdout 之后，让 Agent 能区分正常输出与报错。
# ============================================================
"""SafeTerminalTool — sandboxed shell execution with command blacklist."""
# 安全终端工具，带黑名单的沙箱shell执行

import subprocess                                      # 进程执行 Shell 命令的核心
from pathlib import Path                               # base_dir 类型
from typing import Type                                 # Type[BaseModel] 类型注解
from langchain_core.tools import BaseTool              # 工具基类
from pydantic import BaseModel, Field                  # 入参 schema 定义

# 高危命令黑名单：命中任一子串即拒绝执行。按"破坏类型"分组（删库 / 格盘 / fork 炸弹 / 权限 / 关机）。
# 注意：仅子串匹配，可被变量拼接/编码绕过——纵深防御的一层，不能作为唯一安全依赖，非常容易绕过
# 示例绕过：cmd="r""m -rf /"，或者变量拼接、换行分割命令，黑名单检测直接失效
BLACKLISTED_COMMANDS = [

    # ── 删库类：递归强删根/系统目录，不可逆 ──
    "rm -rf /",                  # Linux 根目录递归强删
    "rm -rf /*",                 # 同上，带通配

    # ── 格盘类：格式化文件系统 ──
    "mkfs",                      # Linux 格式化文件系统
    "dd if=",                    # dd 原始磁盘写入（可写零抹盘）

    # ── fork 炸弹：进程指数级 fork 拖死系统 ──
    ":(){:|:&};:",               # Bash 经典 fork 炸弹

    # ── 权限类：全盘改权限导致系统不可用 ──
    "chmod -R 777 /",            # 全盘可写可执行，系统配置失效

    # ── 关机/重启类：让 Agent 单方面停机 ──
    "shutdown",                  # 关机
    "reboot",                    # 重启
    "halt",                      # 停机
    "poweroff",                  # 关电源

    # ── Windows 等价高危命令 ──
    "format c:",                 # Windows 格式化 C 盘
    "del /f /s /q c:",           # Windows 强删 C 盘全部文件

]

# TerminalInput（终端入参 schema）：约束 Agent 传给工具的参数结构。
class TerminalInput(BaseModel):
    # 待执行的 Shell 命令串，必填
    command: str = Field(description="The shell command to execute")  

# SafeTerminalTool（沙箱终端工具）：LangChain BaseTool 子类，_run 是 Agent 实际调用的入口。
class SafeTerminalTool(BaseTool):
    name: str = "terminal"
    description: str = (
        "Execute shell commands in a sandboxed environment. "
        "The working directory is restricted to the project root. "
        "Use this for file operations, installing packages, running scripts, etc."
    )
    args_schema: Type[BaseModel] = TerminalInput 

    # 保存项目根目录字符串，作为命令执行时的工作目录cwd
    # 同时作为沙箱锚点：subprocess.cwd 锁定到这里，Agent 出不去此目录
    root_dir: str = ""  

    # _is_safe（安全检查）：命令转小写后与黑名单逐条子串匹配。命中任一即判不安全，返回布尔值
    def _is_safe(self, command: str) -> bool:

        # 转小写 + 去首尾空白：让 BlackLIST 的大小写差异失效
        cmd_lower = command.lower().strip()  

        # 逐条子串匹配
        for blocked in BLACKLISTED_COMMANDS:  
            # 只要黑名单字符串是命令的子串，直接判定不安全，返回False
            if blocked in cmd_lower:
                return False

        # 没有命中任何黑名单，判定安全
        return True 

    # _run（执行命令）：Agent 调用 terminal 工具时实际跑这里。先安检 → subprocess → 整理输出 → 截断。
    def _run(self, command: str) -> str:

        # 安检未过：直接返回拒绝信息，不执行
        if not self._is_safe(command):  
            # 告诉 Agent 命令被拦，让它换方案
            return f"❌ Command blocked for safety: {command}"  
        
        try: 
            # subprocess.run：执行 Shell 命令
            result = subprocess.run( 
                command,
                shell=True,           # 开启shell解析，能支持管道、通配符等shell语法（安全风险源头）
                cwd=self.root_dir,    # 锁定工作目录到项目根：Agent 出不去此目录
                capture_output=True,  # 捕获stdout标准输出、stderr标准错误
                text=True,            # 输出以文本字符串返回，而不是bytes二进制
                timeout=30,           # 命令最大执行超时时间30秒，防止死循环命令卡死Agent
                encoding="utf-8",     # 显式 UTF-8：命令输出含中文时不乱码
                errors="replace",     # 无法用utf8解码的字符直接替换，避免读取输出时报编码崩溃
            )

            # 先拿到正常标准输出
            output = result.stdout

            # 如果存在错误输出，追加到结果里，一并返回给大模型
            if result.stderr: 
                output += f"\n[stderr]: {result.stderr}"

            # 命令执行成功但是没有任何输出，给LLM友好提示，避免返回空字符串让 Agent 以为工具坏了
            if not output.strip(): 
                output = "(command completed with no output)"

            # 输出过长截断：防一条命令的输出爆掉后续 LLM 上下文窗口
            if len(output) > 5000:
                # 截断 + 标记，Agent 知道被裁了
                output = output[:5000] + "\n...[truncated]" 

            # 整理好的输出回给 Agent
            return output  

        # 30s 超时：返回明确错误，Agent 可改用更轻的命令
        except subprocess.TimeoutExpired:  
            return "❌ Command timed out (30s limit)"
        # 其他异常（如命令不存在）：返回错误信息，不抛异常打断 Agent
        except Exception as e:  
            return f"❌ Error: {str(e)}"


# create_terminal_tool（建终端工具）：工厂函数，注入 root_dir 后返回配好的实例。
def create_terminal_tool(base_dir: Path) -> SafeTerminalTool:
    return SafeTerminalTool(root_dir=str(base_dir))  # root_dir=项目根，subprocess.cwd 会锁定到这里


# ────────────────────────────────────────────────────────────────────────────
# 安全模型总结：黑名单（拦截已知高危）+ cwd 锁定（限定工作目录）+ 超时/截断（防资源耗尽）。
# 这是"纵深防御"的一层：黑名单可被绕过，真要隔离应配容器/虚拟机；本项目本地单用户场景足够。
# ────────────────────────────────────────────────────────────────────────────