# ============================================================
# files.py — Monaco 编辑器的文件桥 + 技能列表 API。
# 职责：GET/POST /api/files 读写文件 + GET /api/skills 列技能，全部给前端 InspectorPanel 用。
# 安全设计（本项目文件安全的核心，三层防线）：
#   ① 目录白名单 ALLOWED_PREFIXES：只允许 workspace/ memory/ skills/ knowledge/ 四个目录；
#   ② 根文件白名单 ALLOWED_ROOT_FILES：只允许 SKILLS_SNAPSHOT.md 这一个根级文件；
#   ③ resolve() 后必须仍以 BASE_DIR 开头：防 ../ 路径穿越（即便白名单目录内的 ../ 也拦）。
# 关键机制：保存 memory/MEMORY.md 成功后自动触发记忆索引重建——前端改记忆 → RAG 索引实时生效的闭环。
#
# 数据形态：
#   GET /files?path=memory/MEMORY.md → {"path": "memory/MEMORY.md", "content": "..."}
#   POST /files {path, content}     → {"path": "...", "status": "saved"}
#   GET /skills                    → {"skills": [{"name","path","description"}, ...]}
# ============================================================
"""GET/POST /api/files + GET /api/skills — File read/write for Monaco editor."""

from pathlib import Path                               # 路径归一化与越界检查

import yaml                                            # 解析 SKILL.md frontmatter
from fastapi import APIRouter, HTTPException            # 路由 + 异常
from pydantic import BaseModel                          # 请求体 schema

# 创建路由对象，用来注册接口
router = APIRouter()

# BASE_DIR = backend/：文件读写的沙箱锚点，所有路径都必须解析到此目录内
BASE_DIR = Path(__file__).resolve().parent.parent

# 可编辑目录白名单（相对 backend/）：只允许这四个目录被前端读写
# 人格/记忆/技能/知识库四区
ALLOWED_PREFIXES = ["workspace/", "memory/", "skills/", "knowledge/"]  

# 根级文件白名单：除四个目录外，只允许 SKILLS_SNAPSHOT.md 这一个根级文件被访问
ALLOWED_ROOT_FILES = {"SKILLS_SNAPSHOT.md"}  # 技能快照（自动生成，但前端要看）


# _validate_path（路径校验，防御路径穿越攻击）：白名单目录 + 路径穿越检查，是整个文件 API 的安全闸门。
# 两道检查：① 前缀必须在白名单（目录或根文件）② resolve 后仍以 BASE_DIR 开头（防穿越）。
# 任一不过抛 403，绝不返回任何文件内容。
#
# 为什么两道都做：
#   ① 白名单：限定可访问目录范围，workspace/memory/skills/knowledge 是 Agent 人格与记忆的载体，
#      前端编辑这些是合理的；其他目录（如 sessions/ 含用户隐私、tools/ 是代码）不该被前端乱改。
#   ② 穿越检查：即便路径前缀在白名单（如 "memory/../sessions/xxx"），resolve 后会跑出 memory/
#      到 sessions/——第二道 resolve 检查拦的就是这种"披着白名单外衣的穿越"。
#
# 返回值：校验通过的绝对路径，调用方据此 read/write，无需再校验。
def _validate_path(rel_path: str) -> Path:
    """Validate and resolve file path within allowed directories."""

    # 统一路径分隔符，去掉开头多余的 ./
    normalized = rel_path.replace("\\", "/").lstrip("./")  # 跨平台归一化 + 去前导点斜杠

    # 白名单检查：前缀必须是四个允许目录之一，或根级白名单文件
    if not (  
        any(normalized.startswith(prefix) for prefix in ALLOWED_PREFIXES)  # 目录白名单
        or normalized in ALLOWED_ROOT_FILES  # 根文件白名单
    ):
        # 非白名单 → 403 拒绝访问
        raise HTTPException(status_code=403, detail=f"Access denied: {rel_path}")  

    # 拼接成绝对路径并resolve解析
    full_path = (BASE_DIR / normalized).resolve() 

    # 二次防护：解析后的绝对路径，必须仍然在BASE_DIR内部，防止 ../ 跳出项目文件夹
    if not str(full_path).startswith(str(BASE_DIR)):
        # 穿越 → 403 拒绝
        raise HTTPException(status_code=403, detail="Path traversal detected")  

    # 校验通过的绝对路径
    return full_path  

# read_file（依规读取文件）：GET /api/files?path=。校验路径 → 存在检查 → 读内容返回。
# 不存在抛 404，越界/非白名单在 _validate_path 抛 403——前端据状态码区分错误类型。
# 返回 {path, content}，Monaco 拿 content 渲染编辑器；path 原样回传便于前端高亮当前文件。
# content 全量返回（无截断），编辑器需要完整内容才能正常编辑保存。
@router.get("/files")
async def read_file(path: str):

    # 调用路径校验函数，做安全检查，防止路径穿越攻击
    file_path = _validate_path(path)  

    # 判断文件不存在，抛出404
    if not file_path.exists():  
        raise HTTPException(status_code=404, detail=f"File not found: {path}")  # 404
    
    # 读取文本，utf-8编码，读取全部内容
    content = file_path.read_text(encoding="utf-8")  # 显式 UTF-8

    # 返回json给前端，带回原始路径和文件内容（给Monaco编辑器渲染）
    return {"path": path, "content": content}


# FileSaveRequest（保存请求体）：约束前端传 path + content。
# path 须过 _validate_path 白名单校验；content 是整文件新内容（覆盖写，非增量）。
class FileSaveRequest(BaseModel):
    path: str        # 要保存的相对路径
    content: str     # 文件的全部文本内容

# save_file（保存文件）：POST /api/files。校验路径 → 建父目录 → 写盘 → 若是 MEMORY.md 触发索引重建。
#
# 为什么保存 MEMORY.md 要触发索引重建：
#   RAG 模式下 Agent 不再读 MEMORY.md 全文，而是从向量索引检索片段。若用户在 Monaco 改了
#   MEMORY.md 但索引没更新，Agent 检索到的还是旧记忆——闭环就断了。这里检测到保存即重建，
#   保证"前端改记忆 → RAG 检索立即用新内容"，无需重启服务。
#
# 失败策略：索引重建失败静默（pass），不回滚已成功的文件保存——保存是用户的主操作不能被索引层拖垮。
@router.post("/files")
async def save_file(request: FileSaveRequest):

    # 同样先做路径安全校验
    file_path = _validate_path(request.path)
    # 确保父目录存在（新建技能时 skills/xxx/ 可能没有）
    file_path.parent.mkdir(parents=True, exist_ok=True)  
    # 把前端传过来的content写入磁盘，覆盖原有文件
    file_path.write_text(request.content, encoding="utf-8")

    # ── 关键机制：保存 MEMORY.md 触发记忆索引重建 ──
    # 前端在 Monaco 改完 MEMORY.md Ctrl+S → 这里检测到 → 重建 RAG 向量索引。
    # 这是"改记忆立即生效"闭环的落点：无需重启，下次对话 RAG 检索的就是新内容。

    # 标准化路径格式，统一斜杠，去掉开头多余的./
    normalized = request.path.replace("\\", "/").lstrip("./")

    # ✨特殊分支：如果本次修改的文件是 memory/MEMORY.md
    if normalized == "memory/MEMORY.md": 

        try:  # 触发索引重建（失败静默——不让索引层拖垮保存）
            from graph.memory_indexer import get_memory_indexer  # 延迟 import

            # 拿单例索引器
            indexer = get_memory_indexer(BASE_DIR)  

            # 重建向量库+BM25索引 + 落盘 storage/memory_index/
            indexer.rebuild_index()

        # 重建索引失败，直接吞掉异常，不报错。
        except Exception: 
            pass  # 文件已经保存成功，索引更新失败不阻断主流程

    # 返回保存成功信息
    return {"path": request.path, "status": "saved"}


# list_skills（技能列表）：GET /api/skills。扫 skills/ 各子目录的 SKILL.md，解析 frontmatter 返回。
# 给前端 InspectorPanel Skills Tab 展示技能卡片（name + description + 可点击 path）。
#
# 与 skills_scanner.scan_skills 的区别：
#   - scan_skills：启动时全量扫，生成 SKILLS_SNAPSHOT.md 注入 System Prompt（给 Agent 看）；
#   - list_skills：前端请求时扫，返回技能列表给 InspectorPanel 展示（给用户看）。
# 两者都解析 frontmatter，但消费方不同——一个喂 LLM，一个喂 UI。
#
# 容错：单个 SKILL.md 解析失败用默认 name（目录名）/description（空），不阻断整个列表。
# sorted(iterdir) 让技能顺序稳定，便于前端列表 diff 与回归测试。
@router.get("/skills")
async def list_skills():
    """Scan skills/ directory and return skill list with name, path, description."""

    # 技能文件夹的绝对路径
    skills_dir = BASE_DIR / "skills" 

    # 如果skills文件夹不存在，直接返回空的技能数组
    if not skills_dir.exists():
        return {"skills": []}

    # 用来存放所有技能信息的列表，每个元素是字典，包含技能的名称、路径和描述
    skills: list[dict[str, str]] = []  

    # 遍历 skills 目录下所有子文件夹，sorted 保证顺序稳定
    for skill_dir in sorted(skills_dir.iterdir()):

        # 只处理文件夹，跳过里面的普通文件
        if not skill_dir.is_dir():
            continue

        # 拼接：每个技能文件夹里面必须有 SKILL.md
        skill_md = skill_dir / "SKILL.md"

        # 如果这个技能目录没有SKILL.md，直接跳过这个技能
        if not skill_md.exists():
            continue

        # 默认技能名称 = 文件夹名字
        name = skill_dir.name
        # 默认 description 空
        description = ""  

        # 相对路径，前端用来读取这个md文件
        rel_path = f"skills/{name}/SKILL.md"

        # 解析 YAML frontmatter 拿真正的 name/description（覆盖默认值）
        try:  
            # 读取SKILL.md全部文本
            text = skill_md.read_text(encoding="utf-8") 

            # 判断文件开头是不是 --- ，这是YAML frontmatter（md文件头部元信息）
            if text.startswith("---"):  # 有 frontmatter
                # 切三段，格式示例：--- yaml内容 --- 正文
                parts = text.split("---", 2)  
                # 分割后必须至少3段，才代表有完整frontmatter
                if len(parts) >= 3:
                    # 把中间那一段解析成yaml字典
                    meta = yaml.safe_load(parts[1])

                    # 解析成功并且是字典，才读取自定义name和description
                    if isinstance(meta, dict):
                        # 优先用yaml里面写的name，没有就保留文件夹名
                        name = meta.get("name", name)
                        # 读取yaml里的描述
                        description = meta.get("description", "") 

        # 任何异常（yaml解析失败、编码错误等）直接忽略，不中断整个接口
        except Exception:
            pass

        # 把当前技能信息加入列表
        skills.append({"name": name, "path": rel_path, "description": description})

    # 返回技能列表给前端
    return {"skills": skills}  