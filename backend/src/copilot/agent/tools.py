"""Agent 的工具。

四个工具，对应 plan.md M7 的清单：
    search_kb          检索知识库（**自动带当前用户的 owner 过滤**）
    save_requirement   记一条需求，并告诉模型还缺什么
    generate_plan      用 Pydantic 约束的结构化输出生成配置清单
    export_excel       落成 xlsx 供下载

三条贯穿的规矩：

1. **工具的入参里绝不出现 user_id。** 它从 `ctx.deps` 来，只可能是 cookie 里那个人。
   让它变成入参 = 一句 prompt injection 就能读别人的私有文档。
2. **工具失败要返回一句人话，不要抛异常。** 抛出去会让整轮对话崩掉；
   返回「检索失败，请换个说法」的话，Agent 还能继续。这就是 M7
   「单工具报错不能整轮崩」的落地方式。
3. **工具返回给模型的文本要短。** 它每一个字都会进下一轮的 prompt。
   检索结果给正文，不给整块 JSON；清单生成只回摘要，明细留在库里。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from pydantic_ai import RunContext

from copilot.agent.checklist import REQUIREMENT_FIELDS, Checklist
from copilot.agent.deps import AgentDeps
from copilot.config import get_settings
from copilot.retrieve import search

logger = logging.getLogger(__name__)

# 正文里的图片标记 `[图3]`——build_context() 已经重编过号
_IMG_RE = re.compile(r"\[图(\d+)\]")


async def search_kb(ctx: RunContext[AgentDeps], query: str) -> str:
    """检索旺店通旗舰版 ERP 知识库，返回带编号来源的原文片段。

    Args:
        query: 检索用的问题或关键词。用中文，尽量具体（含界面名、功能名更准）。
    """
    deps = ctx.deps
    try:
        result = await search(
            deps.session,
            query,
            deps.embedder,
            deps.reranker,
            # ⚠️ 隔离红线：owner 过滤只能来自 deps，不能是入参
            user_id=deps.user_id,
        )
    except Exception as e:  # noqa: BLE001 - 见文件头第 2 条
        logger.warning("search_kb 失败 query=%r：%s", query[:60], e, exc_info=True)
        return "检索失败了（外部服务异常）。可以换个说法再试一次，或者先问别的。"

    if result.is_empty:
        return f"知识库里没有检索到与「{query}」相关的内容。"

    bundle = result.build_context()
    renumbered = deps.merge_citations([c.to_dict() for c in result.citations])

    # 把本次检索的 [n] 换成本轮全局编号，否则一轮里检索两次会出现两个 [1]
    mapping = {old["n"]: new["n"] for old, new in zip(
        [c.to_dict() for c in result.citations], renumbered, strict=True
    )}
    text = bundle.text
    for old_n in sorted(mapping, reverse=True):  # 从大到小替换，避免 [1]→[10] 又被再替一次
        text = text.replace(f"[{old_n}] 来源：", f"[{mapping[old_n]}] 来源：")

    # 配图也要并进本轮总表，并把正文里的 [图N] 改成全局号
    if bundle.images:
        offset = len(deps.images)
        for img in bundle.images:
            deps.images.append({"n": img["n"] + offset, "url": img["url"]})
        if offset:
            text = _IMG_RE.sub(lambda m: f"[图{int(m.group(1)) + offset}]", text)

    deps.retrieved.append(text)
    return text


async def save_requirement(ctx: RunContext[AgentDeps], field: str, value: str) -> str:
    """记录一条客户的实施需求，返回还缺哪些信息。

    Args:
        field: 需求字段名，只能是这几个之一：
            platforms（对接平台）、shop_count（店铺数量）、
            warehouse_mode（仓库模式）、warehouse_count（仓库数量）、
            daily_orders（日均单量）、logistics（物流商）、specials（特殊业务）
        value: 客户说的原话或归纳后的值，中文。
    """
    if field not in REQUIREMENT_FIELDS:
        # 不抛异常：告诉模型正确的取值范围，它下一步就能改对（比整轮崩掉好得多）
        return f"没有 `{field}` 这个字段。可用字段：{'、'.join(REQUIREMENT_FIELDS)}"

    value = (value or "").strip()
    if not value:
        return f"`{field}` 的值是空的，没有记录。"

    setattr(ctx.deps.profile, field, value[:200])
    label = REQUIREMENT_FIELDS[field][0]
    gaps = ctx.deps.profile.missing()
    if not gaps:
        return f"已记录「{label}：{value}」。需求已收集齐，可以调用 generate_plan 生成方案了。"
    nxt = REQUIREMENT_FIELDS[gaps[0]]
    return (
        f"已记录「{label}：{value}」。\n"
        f"还缺 {len(gaps)} 项：{'、'.join(REQUIREMENT_FIELDS[f][0] for f in gaps)}。\n"
        f"接下来问这个：{nxt[1]}"
    )


async def generate_plan(ctx: RunContext[AgentDeps]) -> str:
    """根据已收集的需求生成《实施配置方案》。需求收集齐之后再调。"""
    deps = ctx.deps
    gaps = deps.profile.missing()
    if len(gaps) > 2:
        # 关键信息缺太多时硬生成，只会得到一份「按需配置」的废纸
        return (
            "需求还差得多，先别生成。缺：" + "、".join(REQUIREMENT_FIELDS[f][0] for f in gaps)
        )

    # 惰性导入：planner 会建一个子 Agent，而 tools 在 agent.py 里被引用，
    # 顶层互相 import 会成环
    from copilot.agent.planner import build_checklist

    try:
        checklist = await build_checklist(deps)
    except Exception as e:  # noqa: BLE001
        logger.warning("generate_plan 失败：%s", e, exc_info=True)
        return "生成方案时出错了。可以让我再试一次。"

    deps.checklist = checklist
    lines = [f"已生成《{checklist.title}》，共 {len(checklist.items)} 项配置：", ""]
    for it in checklist.items[:8]:
        lines.append(f"- [{it.priority}] {it.area} → {it.item}：{it.value}")
    if len(checklist.items) > 8:
        lines.append(f"- …另有 {len(checklist.items) - 8} 项")
    if checklist.open_questions:
        lines += ["", "需要人工确认：" + "；".join(checklist.open_questions[:3])]
    lines += ["", "接下来可以调用 export_excel 导出成 xlsx。"]
    return "\n".join(lines)


async def export_excel(ctx: RunContext[AgentDeps]) -> str:
    """把已生成的配置方案导出成 xlsx，返回下载地址。"""
    deps = ctx.deps
    if deps.checklist is None:
        return "还没有方案可以导出，先调用 generate_plan。"

    s = get_settings()
    rel = f"{deps.user_id}/{deps.conversation_id}.xlsx"
    path = s.export_path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _write_xlsx(path, deps.checklist)
    except Exception as e:  # noqa: BLE001
        logger.warning("导出 xlsx 失败：%s", e, exc_info=True)
        return "导出文件时出错了。方案本身已经生成，可以再试一次导出。"

    deps.download_url = f"/api/conversations/{deps.conversation_id}/export"
    # 告诉模型「已经有下载入口了」，但别让它自己编 URL——真正的地址由前端从
    # data-download 片段拿（见 routes/chat.py）
    return "已导出 xlsx，页面上会出现下载按钮。不要在回答里自己拼下载链接。"


def _write_xlsx(path, checklist: Checklist) -> None:
    """写 xlsx。列宽和换行都得设——不设的话打开是一列挤在一起的天书。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "实施配置方案"

    ws["A1"] = checklist.title
    ws["A1"].font = Font(size=14, bold=True)
    ws["A2"] = checklist.summary
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A1:E1")
    ws.merge_cells("A2:E2")
    ws.row_dimensions[2].height = 46

    headers = ["优先级", "模块／路径", "配置项", "建议值", "依据"]
    head_fill = PatternFill("solid", fgColor="EEF2FF")
    for col, name in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col, value=name)
        cell.font = Font(bold=True)
        cell.fill = head_fill

    for i, it in enumerate(checklist.items, start=5):
        for col, val in enumerate(
            [it.priority, it.area, it.item, it.value, it.why], start=1
        ):
            c = ws.cell(row=i, column=col, value=val)
            c.alignment = Alignment(wrap_text=True, vertical="top")

    for col, width in zip("ABCDE", (8, 26, 22, 40, 52), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A5"  # 表头钉住，几十行的清单往下翻还看得见列名

    if checklist.open_questions:
        sheet2 = wb.create_sheet("待确认")
        sheet2["A1"] = "知识库里没有答案、需要人工确认的问题"
        sheet2["A1"].font = Font(bold=True)
        for i, q in enumerate(checklist.open_questions, start=2):
            sheet2.cell(row=i, column=1, value=q).alignment = Alignment(wrap_text=True)
        sheet2.column_dimensions["A"].width = 80

    meta = wb.create_sheet("生成信息")
    meta["A1"] = "生成时间"
    meta["B1"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    meta["A2"] = "生成方式"
    meta["B2"] = "旺店通旗舰版 ERP 知识库助手（依据知识库材料生成，请人工复核）"
    meta.column_dimensions["A"].width = 14
    meta.column_dimensions["B"].width = 60

    wb.save(str(path))


def conversation_export_name(conversation_id: uuid.UUID) -> str:
    """下载时给用户看的文件名。"""
    return f"实施配置方案-{str(conversation_id)[:8]}.xlsx"
