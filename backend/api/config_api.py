# ============================================================
# config_api.py — RAG 模式开关的 HTTP 薄封装。
# 职责：把 config.py 的 get_rag_mode/set_rag_mode 暴露成两个 REST 端点，供前端侧边栏 🗄️ 按钮调用。
# 两个端点：
#   GET  /api/config/rag-mode → 查当前模式（前端进入时拉一次初始化按钮态）
#   PUT  /api/config/rag-mode → 切换模式（前端乐观更新，失败回滚）
# 这是"记忆双模式"的前端入口——切换后下一轮对话 agent.py astream 现读现生效。
# ============================================================
"""GET/PUT /api/config/rag-mode — RAG mode toggle API."""

from fastapi import APIRouter                          # FastAPI 路由
from pydantic import BaseModel                          # 请求体 schema
from config import get_rag_mode, set_rag_mode          # 配置读写实现（config.py）

# 创建路由对象，用来注册接口
router = APIRouter()

# RagModeRequest（PUT 请求体 schema）：约束前端必须传 enabled 布尔字段。
class RagModeRequest(BaseModel):
    enabled: bool    # True=开 RAG 检索模式，False=全文注入 MEMORY.md

# get_rag_mode_endpoint（查询当前 RAG 开关状态）：GET /api/config/rag-mode。
# 前端 store.tsx 挂载时调一次，拿当前 rag_mode 初始化侧边栏按钮态。
@router.get("/config/rag-mode")
async def get_rag_mode_endpoint():

    # 调用config模块读取配置，返回给前端
    return {"rag_mode": get_rag_mode()}

# set_rag_mode_endpoint（修改RAG开关状态）：PUT /api/config/rag-mode {enabled: bool}。
# 前端 toggleRagMode 乐观更新后调此端点持久化；失败则前端回滚 UI 状态。
@router.put("/config/rag-mode")
async def set_rag_mode_endpoint(request: RagModeRequest):
    
    # 调用config模块的函数，写入config.json持久化保存
    set_rag_mode(request.enabled)
    # 返回最新状态给前端
    return {"rag_mode": request.enabled} 
