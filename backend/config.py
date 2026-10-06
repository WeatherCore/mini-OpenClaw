# ============================================================
# config.py — 全局运行时配置的"读改写"层（JSON 文件持久化）。
# 职责单一：把 backend/config.json 读写封装成 Python 函数，目前只管 rag_mode 一个开关。
# 设计取舍：
#   ① 读永远带默认值兜底——文件缺失/损坏不会让服务崩，坏配置回退成 {rag_mode: False}；
#   ② 无内存缓存，每次都现读盘（配置项少、调用频率低，可接受；好处是 PUT 改完下轮对话即生效）。
# 谁在用：api/config_api.py（HTTP 开关）读写、graph/agent.py astream 每轮现读 rag_mode。
# ============================================================
"""Global configuration management — JSON-based persistence."""

import json                                            # 配置以 JSON 文本落盘
from pathlib import Path                               # 跨平台路径
from typing import Any                                 # dict[str, Any] 类型注解（PRD 强制 Type Hinting）

# 配置文件路径：在当前文件所在目录下的 config.json
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

# 默认配置字典，项目初始配置
# 新增配置项时在这里加一行即可，load_config 的合并逻辑会保证旧文件缺失该键也有默认值
_DEFAULT_CONFIG: dict[str, Any] = {
    "rag_mode": False,        # RAG 检索模式开关，默认关闭：False=全文注入 MEMORY.md，True=按需向量检索
}

# load_config（读配置）：从磁盘读 config.json，缺/坏则回退默认值。
# 返回的是新 dict，调用方改它不会污染模块级 _DEFAULT_CONFIG。
def load_config() -> dict[str, Any]:
    """Load configuration from disk, returning defaults if missing."""

    # 从磁盘加载配置文件不存在（首次启动/被误删）→ 返回默认值的副本，绝不让上层拿到 None
    if not CONFIG_FILE.exists():
        return dict(_DEFAULT_CONFIG)  # 文件缺失：回退默认值的副本，调用方改它不污染模块级常量
    
    try:  
        # 读取配置文件文本，utf-8编码
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) 

        # 合并配置：默认配置为基底，用文件里的配置覆盖同名key（保留新增key）
        return {**_DEFAULT_CONFIG, **data}
        # 这是 Python 字典解包合并语法，如果两边有相同 key，后面的 data 会覆盖前面 _DEFAULT_CONFIG 的值

    # 任何异常（文件损坏、json语法错误、权限问题），回退默认配置
    except Exception:
        return dict(_DEFAULT_CONFIG)

# save_config（写配置）：覆盖式写盘。显式 UTF-8 + ensure_ascii=False，中文原样保存
def save_config(config: dict[str, Any]) -> None:
    """Persist configuration to disk."""

    # 将传入的配置字典写入 config.json 文件持久化存储
    CONFIG_FILE.write_text(  # 覆盖式写盘：整体替换，不会丢其他键（调用前已 load 全量）
        # dumps序列化字典为json字符串，ensure_ascii=False支持中文，indent=2格式化缩进
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# get_rag_mode（查 RAG 开关）：薄封装 load_config，返回 bool。
# agent.py astream 每轮调用一次——决定本轮拼不拼 MEMORY.md / 要不要走检索分支
def get_rag_mode() -> bool:
    """Get current RAG mode setting."""

    # 获取当前RAG模式开关状态
    # bool() 强转：防御 config.json 里被手写成 "false"/0 等非布尔真值的脏数据
    return bool(load_config().get("rag_mode", False))


# set_rag_mode（改 RAG 开关）：经典的读-改-写三步。前端 PUT /api/config/rag-mode 走这里。
# 注意无并发锁：同会话并发改会有覆盖风险（本项目单用户本地场景无碍）。
def set_rag_mode(enabled: bool) -> None:
    """Set RAG mode on/off."""

    config = load_config()        # 第 1 步：读当前全量配置
    config["rag_mode"] = enabled  # 第 2 步：在内存里改这一项
    save_config(config)           # 第 3 步：整体覆盖写回（不会丢其他键）
