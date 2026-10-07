# ============================================================
# skills_scanner.py — skills 技能快照生成器（Instruction-following 技能系统的入口）。
# 职责：递归扫描 backend/skills/*/SKILL.md，解析 YAML frontmatter，生成 XML 风格的 SKILLS_SNAPSHOT.md。
# 生成的快照会被 prompt_builder 拼进 System Prompt 头部，Agent 据此"感知"自己有哪些技能可用。
# 设计取舍：location 用相对路径 ./backend/skills/<name>/SKILL.md，Agent 拿它直接喂给 read_file 读说明书。
# 谁在用：app.py lifespan 启动时调一次（scan_skills(BASE_DIR)）；坏单个 SKILL.md 只警告不中断扫描。
#
# 生成的快照格式（拼进 System Prompt 后 Agent 看到的是这样）：
#   <available_skills>
#     <skill>
#       <name>get_weather</name>
#       <description>获取指定城市的实时天气信息</description>
#       <location>./backend/skills/get_weather/SKILL.md</location>
#     </skill>
#     ...
#   </available_skills>
#
# SKILL.md 文件结构（被扫描的源）：
#   ---
#   name: get_weather                 ← YAML frontmatter，本函数解析这一段
#   description: 获取指定城市实时天气
#   ---
#   # 技能正文（Agent 用 read_file 读到的内容）
#   ## 操作步骤 ...
#
# 容错策略：单个 SKILL.md 解析失败只 print 警告，继续扫下一个——一个坏技能不让整个系统起不来。
# ============================================================
"""Skills Scanner — Scan skills/ directory and generate SKILLS_SNAPSHOT.md."""
# 技能扫描器。扫描skills目录下所有SKILL.md，自动生成SKILLS_SNAPSHOT.md技能清单快照文件

from pathlib import Path                               # 路径拼接，跨平台
import yaml                                            # 解析 SKILL.md 顶部的 YAML frontmatter

# scan_skills（扫描技能）：递归找所有 SKILL.md → 解析 frontmatter → 拼 XML 快照 → 写盘 SKILLS_SNAPSHOT.md。
# 返回快照字符串（app.py 拿不到也不报错，快照已落盘）。
# 四步：① 确保目录存在 ② 逐个解析 frontmatter ③ 拼 XML 快照 ④ 写盘 + 日志。
def scan_skills(base_dir: Path) -> str:
    """Scan all SKILL.md files and generate SKILLS_SNAPSHOT.md."""

    # 拼接技能文件夹路径：项目根目录/skills，所有技能都放在这个目录下，一个技能一个子文件夹
    skills_dir = base_dir / "skills" 
    # 快照文件输出路径，生成的技能清单就写进这个 md 文件（prompt_builder 会读它）
    snapshot_path = base_dir / "SKILLS_SNAPSHOT.md"

    # ── 第 1 步：确保 skills/ 目录存在 ──
    # 如果 skills 文件夹不存在，自动创建；parents=True支持一次性创建多级目录
    if not skills_dir.exists():  # skills/ 不存在：自动建空目录，避免 rglob 报错
        skills_dir.mkdir(parents=True)  # 建目录，下次放技能即用

    # ── 第 2 步：逐个解析 SKILL.md 的 frontmatter ──
    
    # 空列表，用来存放所有解析成功的技能元信息（name、description、location）
    skills = []

    # rglob 递归遍历 skills 目录，查找所有名为 SKILL.md 的文件；sorted保证每次扫描顺序固定，快照内容稳定
    for skill_md in sorted(skills_dir.rglob("SKILL.md")): 
        try: 
            # 读取SKILL.md全部文本，utf8编码，支持中文
            content = skill_md.read_text(encoding="utf-8") 

            # 判断文件头部是否是YAML frontmatter（markdown技能文件的约定格式，用---分隔元数据）
            if content.startswith("---"): 

                # 用---分割，最多分割成2次，得到三段：[空字符串, yaml元数据, 正文内容]
                parts = content.split("---", 2)

                # 三段齐全（避免格式残缺）
                if len(parts) >= 3:
                    # 解析 frontmatter 成 dict（safe_load 防任意代码执行）
                    meta = yaml.safe_load(parts[1])  

                    if meta:  # 解析成功
                        # 生成相对路径，写入快照，方便Agent后续调用read_file工具读取这个SKILL.md详情
                        # skill_md.parent.name 值就是skills的名字，如：get_weather_open
                        rel_path = f"./backend/skills/{skill_md.parent.name}/SKILL.md" 

                        # 收集技能元数据
                        skills.append({
                            # 优先读取yaml里的name；如果没配置name，就使用文件夹名作为技能名称
                            "name": meta.get("name", skill_md.parent.name), 
                            # 读取技能描述，yaml没有description就填空字符串
                            "description": meta.get("description", ""), 
                            # Agent 据此 read_file 读说明书
                            "location": rel_path,  
                        })

        # 单个SKILL.md解析失败只打印警告，不中断整个扫描流程，保证其他技能正常加载
        except Exception as e: 
            print(f"⚠️ Error scanning {skill_md}: {e}")

    # ── 第 3 步：拼 XML 风格快照 ──
    # 用 XML 标签而非 JSON/Markdown：大模型 LLM 对 XML 标签的结构化识别更稳，不易混淆字段边界

    # XML 根节点
    lines = ["<available_skills>"]  
    # 每个技能一组 <skill> 标签
    for s in skills:  
        lines.append("  <skill>")  # 技能块开始
        lines.append(f"    <name>{s['name']}</name>") 
        lines.append(f"    <description>{s['description']}</description>")  
        lines.append(f"    <location>{s['location']}</location>") 
        lines.append("  </skill>")  # 技能块结束
    lines.append("</available_skills>")  # XML 根节点闭合

    # ── 第 4 步：写盘 + 日志 ──
    # 把列表里所有行拼接成完整字符串
    snapshot = "\n".join(lines)  
    # 落盘，将生成的快照写入 SKILLS_SNAPSHOT.md 持久化保存
    snapshot_path.write_text(snapshot, encoding="utf-8")

    # 启动日志，方便确认扫到几个技能
    print(f"📋 Skills snapshot: {len(skills)} skills found")  

    # 返回快照字符串（调用方 app.py 不用，但留着便于测试）
    return snapshot  