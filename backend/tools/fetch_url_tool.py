# ============================================================
# fetch_url_tool.py — Agent 的"浏览器"（联网能力的核心）
# 职责：抓指定 URL 的内容并清洗成 Markdown/JSON 返回给 Agent。
# PRD 明确要求增强：原生 RequestsGetTool 返回原始 HTML、Token 消耗巨大，必须清洗。
# 本工具用 html2text 把 HTML→Markdown（保留链接、丢图片、不折行），把噪音砍到最小。
# 三道防护：15s 超时（防挂死）+ 5000 字符截断（防爆 Token）+ JSON 直返（API 场景不浪费清洗）。
# Agent 用它：查天气 API、抓文档、读在线资料——是"技能说明书"里 fetch_url 步骤的执行体。
#
# 数据形态示例（Agent 拿到的返回长这样）：
#   - HTML 场景（清洗后 Markdown）：
#     ## 标题
#     正文段落……[链接](https://...)
#   - JSON 场景（直接原样返回）：
#     {"city":"北京","temp":25,"weather":"晴"}
#   - 错误场景（统一 ❌ 开头，Agent 能读懂自行调整）：
#     ❌ Request timed out (15s limit)
# ============================================================
"""FetchURLTool — Fetch a URL and return cleaned Markdown content."""

from typing import Type                                 # Type[BaseModel] 类型注解

import html2text                                       # HTML→Markdown 清洗库（PRD 指定的清洗方案）
import requests                                        # HTTP 请求
from langchain_core.tools import BaseTool              # 工具基类
from pydantic import BaseModel, Field                  # 入参 schema


# FetchURLInput 输入模型（入参 schema）：约束 Agent 必须传一个 url 字段。
class FetchURLInput(BaseModel):
    # 待抓取的 URL，必填
    url: str = Field(description="The URL to fetch content from")  

# FetchURLTool（网页抓取工具）：BaseTool 子类，_run 是 Agent 调用入口。
# 两条处理路径按 content-type 分流：
#   - application/json → 原样返回（清洗会破坏 JSON 结构，Agent 要自己解析）
#   - 其他（HTML 居多）→ html2text 清洗成 Markdown 再返回
class FetchURLTool(BaseTool):
    name: str = "fetch_url"    # 工具名（Agent 调用时用，对齐 PRD）
    description: str = (       # 教 Agent 何时用、输入规范
        "Fetch the content of a web page and return it as cleaned Markdown text. "
        "Use this to retrieve information from the internet. "
        "Input should be a valid URL (starting with http:// or https://)."
    )
    args_schema: Type[BaseModel] = FetchURLInput     # 入参 schema，Agent 知道要传 url 字段

    # _run（执行抓取）：GET URL → 按响应类型分流（JSON 直返 / HTML 清洗成 Markdown）→ 截断 → 返回
    def _run(self, url: str) -> str:

        try:
            # 自定义请求头 headers：伪装成浏览器避免被一些站点拒绝
            headers = { 
                "User-Agent": "Mozilla/5.0 (compatible; MiniOpenClaw/0.1)"
            }

            # 发送 GET 请求，15s 超时：防目标站挂死拖垮会话
            resp = requests.get(url, headers=headers, timeout=15)
            # 如果 http 状态码不是 2xx（404、403、500），主动抛出 RequestException 异常，被下方 except 捕获，如果不写这一行，requests 拿到 404 页面不会报错，会继续往下解析 404 的网页内容，这不是我们想要的
            resp.raise_for_status()

            # 拿出响应类型，看决定怎么处理
            content_type = resp.headers.get("content-type", "")  

            # ── 分支 1：JSON 响应就直接返回原始文本，不走 HTML 转 Markdown 逻辑 ──
            # 清洗会破坏 JSON 结构，API 场景 Agent 要的是结构化数据，原样给它自己解析。
            if "application/json" in content_type:
                # 拿到原始 JSON 文本
                text = resp.text  
                if len(text) > 5000:  # 超 5000 字符截断
                    text = text[:5000] + "\n...[truncated]"  # 截断 + 标记

                # 返回原始 JSON 给 Agent 自己解析
                return text  

            # ── 分支 2：HTML 响应用 html2text 清洗成 Markdown ──
            # 原始 HTML 标签噪音占 Token 大头，清洗后保留语义、砍掉标签，省 Token 又好读。
            # 建 HTML→Markdown 转换器
            converter = html2text.HTML2Text()

            # 保留网页里的超链接，大模型能看到链接地址，还能点进去追源
            converter.ignore_links = False
            # 忽略图片。图片的<img>标签对文本 LLM 没有意义，直接丢弃，减少噪音
            converter.ignore_images = True
            # 不自动换行。html2text 默认会自动把长文本拆成固定宽度换行，设置 0 关闭自动换行，输出干净的 Markdown
            converter.body_width = 0

            # 清洗 HTML，得到 Markdown 字符串
            markdown = converter.handle(resp.text)  # 实际清洗

            if len(markdown) > 5000:  # 超 5000 字符截断
                markdown = markdown[:5000] + "\n...[truncated]"  # 截断 + 标记

            # 返回清洗后的 Markdown 给 Agent    
            return markdown  # 返回清洗后的 Markdown 给 Agent
        
        except requests.Timeout:  # 15s 超时：返回明确错误，Agent 可换更快的源
            return "❌ Request timed out (15s limit)"
        except requests.RequestException as e:  # 其他网络异常（连接拒绝/DNS 失败等）
            return f"❌ Fetch error: {str(e)}"


# create_fetch_url_tool（工厂函数，创建并返回工具实例）
# 项目里的规范写法：不直接`FetchURLTool()`实例化，统一通过工厂函数创建，后续如果要加全局初始化、注入配置、开关工具（比如根据前面`rag_mode`配置是否启用联网工具），只需要修改这个函数，不用改到处实例化的代码
def create_fetch_url_tool() -> FetchURLTool:
    return FetchURLTool()  # 直接实例化返回，配好的 name/description 已在类定义里写死