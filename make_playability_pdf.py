# -*- coding: utf-8 -*-
"""生成《狼人杀可玩性提升方案》PDF。

内容来自对 D:\\code\\werewolf-game 的代码审查与可玩性方案讨论（2026-10-17）。
运行：.venv\\Scripts\\python.exe make_playability_pdf.py
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.shapes import Circle, Drawing, Line, Rect, String

OUT = Path(__file__).resolve().parent / "狼人杀可玩性提升方案.pdf"

# ---------- 中文字体 ----------
FONT_NORMAL = "MSYH"
FONT_BOLD = "MSYHBD"


def _register_fonts() -> None:
    candidates = [
        ("MSYH", r"C:\Windows\Fonts\msyh.ttc", 0),
        ("MSYHBD", r"C:\Windows\Fonts\msyhbd.ttc", 0),
    ]
    for name, path, sub in candidates:
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=sub))
    pdfmetrics.registerFontFamily(
        FONT_NORMAL,
        normal=FONT_NORMAL,
        bold=FONT_BOLD,
        italic=FONT_NORMAL,
        boldItalic=FONT_BOLD,
    )


_register_fonts()

# ---------- 样式 ----------
C_MAIN = colors.HexColor("#1f2937")
C_SUB = colors.HexColor("#6b7280")
C_LINE = colors.HexColor("#e5e7eb")
C_BLUE = colors.HexColor("#2563eb")
C_ORANGE = colors.HexColor("#ea580c")
C_GREEN = colors.HexColor("#16a34a")
C_PURPLE = colors.HexColor("#7c3aed")
C_TABLE_HEAD = colors.HexColor("#f1f5f9")

st_title = ParagraphStyle(
    "title", fontName=FONT_BOLD, fontSize=22, leading=30, textColor=C_MAIN, spaceAfter=4
)
st_subtitle = ParagraphStyle(
    "subtitle", fontName=FONT_NORMAL, fontSize=11, leading=16, textColor=C_SUB, spaceAfter=2
)
st_h1 = ParagraphStyle(
    "h1", fontName=FONT_BOLD, fontSize=15, leading=22, textColor=colors.HexColor("#111827"),
    spaceBefore=14, spaceAfter=6,
)
st_h2 = ParagraphStyle(
    "h2", fontName=FONT_BOLD, fontSize=12, leading=18, textColor=colors.HexColor("#334155"),
    spaceBefore=10, spaceAfter=4,
)
st_body = ParagraphStyle(
    "body", fontName=FONT_NORMAL, fontSize=10, leading=15.5, textColor=C_MAIN, spaceAfter=4,
)
st_bullet = ParagraphStyle(
    "bullet", fontName=FONT_NORMAL, fontSize=10, leading=15.5, textColor=C_MAIN,
    leftIndent=12, bulletIndent=2, spaceAfter=3,
)
st_cell = ParagraphStyle(
    "cell", fontName=FONT_NORMAL, fontSize=8.6, leading=12.5, textColor=C_MAIN,
)
st_cell_head = ParagraphStyle(
    "cellHead", fontName=FONT_BOLD, fontSize=8.8, leading=12, textColor=colors.HexColor("#0f172a"),
)
st_note = ParagraphStyle(
    "note", fontName=FONT_NORMAL, fontSize=8.5, leading=13, textColor=C_SUB, spaceAfter=3,
)


def p(text: str, style: ParagraphStyle = st_body, **kwargs) -> Paragraph:
    return Paragraph(text, style, **kwargs)


def table(headers: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    data = [[Paragraph(h, st_cell_head) for h in headers]]
    for row in rows:
        data.append([Paragraph(cell, st_cell) for cell in row])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, C_LINE),
        ("BACKGROUND", (0, 0), (-1, 0), C_TABLE_HEAD),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
    ]
    t.setStyle(TableStyle(style))
    return t


# ---------- 优先级矩阵图 ----------
def matrix_drawing() -> Drawing:
    d = Drawing(520, 330)
    x0, y0, w, h = 70.0, 40.0, 420.0, 240.0

    # 轴
    d.add(Line(x0, y0, x0 + w, y0, strokeColor=colors.HexColor("#334155"), strokeWidth=1.4))
    d.add(Line(x0, y0, x0, y0 + h, strokeColor=colors.HexColor("#334155"), strokeWidth=1.4))
    d.add(String(x0 + w / 2, y0 - 22, "实现成本 →", fontSize=10.5, fontName=FONT_BOLD,
                 textAnchor="middle", fillColor=colors.HexColor("#334155")))
    d.add(String(x0 - 14, y0 + h / 2, "可玩性收益 →", fontSize=10.5, fontName=FONT_BOLD,
                 textAnchor="middle", fillColor=colors.HexColor("#334155"),
                 rotate=90, anchorOffsetOrigin=True))
    d.add(String(x0, y0 - 8, "低", fontSize=9, fontName=FONT_NORMAL,
                 textAnchor="middle", fillColor=colors.HexColor("#94a3b8")))
    d.add(String(x0 + w, y0 - 8, "高", fontSize=9, fontName=FONT_NORMAL,
                 textAnchor="middle", fillColor=colors.HexColor("#94a3b8")))
    d.add(String(x0 - 6, y0, "低", fontSize=9, fontName=FONT_NORMAL,
                 textAnchor="end", fillColor=colors.HexColor("#94a3b8")))
    d.add(String(x0 - 6, y0 + h, "高", fontSize=9, fontName=FONT_NORMAL,
                 textAnchor="end", fillColor=colors.HexColor("#94a3b8")))

    # 中位分隔线
    d.add(Line(x0, y0 + h / 2, x0 + w, y0 + h / 2, strokeColor=colors.HexColor("#cbd5e1"),
               strokeWidth=0.8, strokeDasharray=[4, 4]))
    d.add(Line(x0 + w / 2, y0, x0 + w / 2, y0 + h, strokeColor=colors.HexColor("#cbd5e1"),
               strokeWidth=0.8, strokeDasharray=[4, 4]))
    d.add(String(x0 + w, y0 + h / 2 + 12, "快速见效区", fontSize=8.5, fontName=FONT_NORMAL,
                 textAnchor="end", fillColor=colors.HexColor("#94a3b8")))

    def px(cost: float, benefit: float):
        return x0 + cost * w, y0 + benefit * h

    points = [
        ("A1 警长与徽流", 0.45, 0.85, C_BLUE, 4, 4),
        ("B1 悍跳/诈身份引导", 0.30, 0.80, C_ORANGE, 4, 4),
        ("C3 自动复盘报告", 0.40, 0.75, C_GREEN, 4, 4),
        ("A4 白狼王/狼美人", 0.55, 0.70, C_BLUE, 4, -14),
        ("B3 情绪记忆", 0.60, 0.75, C_ORANGE, 4, 4),
        ("C1 对局回放器", 0.65, 0.80, C_GREEN, 4, 4),
        ("B2 人格档案", 0.30, 0.70, C_ORANGE, 4, -12),
        ("A2 狼人自爆", 0.45, 0.60, C_BLUE, 4, -12),
        ("D1 ELO/胜率天梯", 0.70, 0.60, C_PURPLE, 4, 4),
        ("A3 遗言配置化", 0.12, 0.55, C_BLUE, 4, 0),
        ("D2 防泄漏评测集", 0.25, 0.50, C_PURPLE, 4, -12),
    ]
    for label, cost, benefit, color, dx, dy in points:
        x, y = px(cost, benefit)
        d.add(Circle(x, y, 4.2, fillColor=color, strokeColor=colors.white, strokeWidth=0.8))
        d.add(String(x + dx + 2, y + dy + 3, label, fontSize=8.8, fontName=FONT_BOLD,
                     fillColor=color))

    # 图例
    ly = 6
    d.add(Rect(80, ly, 10, 10, fillColor=C_BLUE))
    d.add(String(96, ly + 1, "板子与规则", fontSize=9, fontName=FONT_NORMAL, fillColor=C_MAIN))
    d.add(Rect(210, ly, 10, 10, fillColor=C_ORANGE))
    d.add(String(226, ly + 1, "角色扮演", fontSize=9, fontName=FONT_NORMAL, fillColor=C_MAIN))
    d.add(Rect(330, ly, 10, 10, fillColor=C_GREEN))
    d.add(String(346, ly + 1, "对局外体验", fontSize=9, fontName=FONT_NORMAL, fillColor=C_MAIN))
    d.add(Rect(450, ly, 10, 10, fillColor=C_PURPLE))
    d.add(String(466, ly + 1, "平衡与持久化", fontSize=9, fontName=FONT_NORMAL, fillColor=C_MAIN))
    return d


# ---------- 内容 ----------
def build() -> None:
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="狼人杀可玩性提升方案",
        author="Doubao",
    )
    story: list = []

    # 封面
    story.append(Spacer(1, 26))
    story.append(p("电子版狼人杀 · 可玩性提升方案", st_title))
    story.append(p("项目：werewolf-game（D:\\code\\werewolf-game）", st_subtitle))
    story.append(p("技术底座：AutoGen 0.7 · 上帝调度器 + 外部状态机 · [ACTION] 协议 · 频道隔离", st_subtitle))
    story.append(p("文档日期：2026-10-17", st_subtitle))
    story.append(Spacer(1, 14))
    story.append(p(
        "本文档基于对项目源码、测试与对局日志的实际检查提出可玩性增强方案，"
        "全部方案均贴合现有架构，不推翻已有的频道隔离与状态机设计。", st_body))

    # 一、现状摘要
    story.append(p("一、现状摘要（已实际验证）", st_h1))
    for line in [
        "架构：模型只负责扮演，胜负、刀口、解药、放逐全部由外部状态机确定性结算；夜间私聊、白天公聊、投票、宣判分频道隔离。",
        "测试：31 个单元测试全部通过（unittest，5.6 秒），覆盖规则结算、频道隔离、行动解析降级、厂商分配与 mock 全流程。",
        "运行：mock 对局（classic9 / seed 7）完整跑通，日志中频道 kind（solo / group / broadcast）与设计声明逐项一致。",
        "未验证项：真实模型行为——[ACTION] 协议遵守率、私聊信息是否会泄漏进公聊、狼人是否会暴露队友，均有待真实对局检验。",
    ]:
        story.append(p(line, st_bullet, bulletText="•"))

    # 二、方案总览
    story.append(p("二、方案总览（按改动层分四组）", st_h1))

    story.append(p("2.1 板子与规则层 —— 改动 machine.py / config.yaml，纯状态机增量", st_h2))
    story.append(table(
        ["方案", "改动点", "可玩性增益", "成本"],
        [
            ["警长与徽流", "白天发言前竞选警长，警长票权 1.5；整数计票改权重票，平票/PK 逻辑随之调整，出局可移交警徽",
             "策略核心层：抢警徽与警徽流验人博弈，好人有组织、狼人有目标", "中"],
            ["狼人自爆", "白天新增自爆动作，吞掉当前发言直接入夜",
             "改变节奏——破冲锋、救队友、藏身份，增加狼队战术选择", "中"],
            ["遗言配置化", "规则可配：狼刀死者是否给遗言（现仅放逐有）",
             "信息量变化直接改变玩法，经典变体的开关", "极低"],
            ["狼队技能板子", "白狼王（自爆带人）、狼美人（被放逐带人）",
             "狼队技能扩展，胜负判定不变，可在现有 apply_shot 上扩展", "中"],
            ["丘比特板子", "第三方情侣阵营（连心同死、第三方胜利）",
             "板子多样性是重开的核心动力；但胜负判定要重写，屠城/屠边口径均需改造", "高"],
            ["首夜保护变体", "女巫首夜必救 / 禁救的配置项",
             "增加开局博弈", "极低"],
        ],
        [88, 172, 195, 60],
    ))

    story.append(p("2.2 角色扮演质量层 —— 改动 prompts.py + 新增记忆，专治 AI 玩家呆板", st_h2))
    story.append(table(
        ["方案", "改动点", "可玩性增益", "成本"],
        [
            ["悍跳/诈身份引导", "给狼人「跳预言家/守卫/女巫」的决策框架，给好人诈身份话术",
             "制造悍跳对跳的戏剧性场面，AI 对局最缺的部分", "低"],
            ["人格档案", "每个座位配性格标签（激进/谨慎/话痨/冷面）注入系统提示",
             "玩家区分度，观战不腻", "低"],
            ["情绪记忆", "只记公开信息：白天谁投过我、谁公开支持/反对我；不记录夜间动作（守卫守护、女巫解救等私密信息）",
             "形成长期恩怨博弈，且不与「私聊不回灌公聊」的隔离原则抵触", "中"],
            ["发言多样性约束", "禁止「我是X号先听发言」式模板开场",
             "真实模型也会趋同，加语气约束后观感差异大", "低-中"],
        ],
        [88, 172, 195, 60],
    ))

    story.append(p("2.3 对局外体验层 —— 复用 logs/latest.json，新前端/工具", st_h2))
    story.append(table(
        ["方案", "改动点", "可玩性增益", "成本"],
        [
            ["对局回放器", "把日志渲染成可回放时间线（按频道着色）",
             "让「看一局」成为可传播的观赏内容；依赖日志留存策略，联机版延后到持久化拍板后", "中"],
            ["自动复盘报告", "每局生成关键转折点 + 胜负归因（谁带崩节奏、谁 carry）",
             "从玩完就散到打完有回味，复盘讨论即二次可玩性", "低-中"],
            ["视角观战", "可选只看某玩家视角（含私聊）",
             "拉新手入坑的观战体验", "中"],
            ["实时比分牌", "存活/出局/明牌身份可视化（复用 render_morning 数据）",
             "联机观战页天然具备（公开信息即裁判视角），无需单独模块，延后", "低"],
        ],
        [88, 172, 195, 60],
    ))

    story.append(p("2.4 平衡与持久化层 —— 数据层", st_h2))
    story.append(table(
        ["方案", "改动点", "可玩性增益", "成本"],
        [
            ["ELO/胜率天梯", "跨局统计狼/好胜率，按板子校准平衡",
             "多局连续游玩的动力 + 平衡性数据；依赖对局持久化（联机设计 4.8），服务将从「打完即散」变为「存局产品」", "中-高"],
            ["防泄漏评测集", "真实对局公聊自动过 assert_public + 关键词审计，量化泄漏率",
             "质量护栏：先保证不漏牌，玩法才有意义", "低"],
            ["难度分级", "同一板子用不同厂商/温度组合区分新手局与高手局",
             "受众分层", "中-高"],
        ],
        [88, 172, 195, 60],
    ))

    # 三、优先级矩阵
    story.append(PageBreak())
    story.append(p("三、优先级矩阵", st_h1))
    story.append(p(
        "横轴为实现成本（低→高），纵轴为可玩性收益（低→高）。"
        "左上角为「高收益、低成本」的优先区。图中坐标为方案讨论时的主观评估示意，非实测数据。", st_body))
    story.append(Spacer(1, 6))
    story.append(matrix_drawing())

    story.append(Spacer(1, 6))
    story.append(p("四、首期推荐（如只做三件事）", st_h1))
    story.append(KeepTogether([
        p("<b>1) 警长与徽流</b> —— 唯一能把一局策略复杂度提升一个档次的纯状态机改动，改动范围可控。", st_body),
        p("<b>2) 悍跳/诈身份引导</b> —— AI 对局最缺的戏剧性就靠它，只改 prompt。", st_body),
        p("<b>3) 防泄漏评测集</b> —— 先给真实对局上这道质检，后面所有玩法扩展才有可靠地基。", st_body),
        p("第二梯队：狼队技能板子（白狼王/狼美人）、丘比特板子（成本高、后置）。以上三件（警长/悍跳/防泄漏）均不动频道隔离架构；对局回放器因依赖持久化决策而延后。", st_body),
    ]))

    # 附注
    story.append(Spacer(1, 8))
    story.append(p("五、附注", st_h1))
    for line in [
        "成本分级与收益评分为主观评估（已在图中标注「示意」），需以实际实现与真实对局验证为准。",
        "所有验证数字（31 个测试、mock 对局结果、日志结构）均来自 2026-10-17 对项目本地的直接检查，无外部来源。",
        "真实模型行为（[ACTION] 协议遵守率、信息泄漏率）是本方案效果的最大不确定因素，建议首期先跑一场真实 6 人局。",
    ]:
        story.append(p(line, st_bullet, bulletText="•"))

    doc.build(story)
    print(f"PDF 已生成: {OUT}")


if __name__ == "__main__":
    build()
