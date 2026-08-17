"""需求档案与实施配置清单的数据形状。

**为什么用 Pydantic 约束而不是让模型自由输出表格**：这份清单最后要落成 xlsx
交给实施顾问照着做。自由文本里少一列、多一个空行都得人工修；而结构化输出
出错时是**解析失败**（能重试），不是「看起来对但缺了一列」。

需求字段是照 ERP 实施真正要问的东西定的（`plan.md` M7 验收里那几项：
平台数 / 仓库结构 / 日单量），不是想象的。**顺序就是追问顺序**——
前面的问题决定后面的答案有没有意义（不知道对接哪些平台，问面单打印方式没用）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 需求档案的字段定义：字段名 → (给用户看的名字, 追问时的提示)
# ⚠️ **这里是唯一的字段清单**。Agent 的工具、追问逻辑、xlsx 表头全从它派生，
# 散成三份的话，加一个字段就得改三处，而漏改的那处不会报错。
REQUIREMENT_FIELDS: dict[str, tuple[str, str]] = {
    "platforms": ("对接平台", "要对接哪些电商平台？（如淘宝、拼多多、抖音、京东）"),
    "shop_count": ("店铺数量", "一共多少个店铺？"),
    "warehouse_mode": ("仓库模式", "仓库是自营、云仓、委外，还是混合？"),
    "warehouse_count": ("仓库数量", "有几个发货仓？"),
    "daily_orders": ("日均单量", "平峰期日均多少单？大促峰值大概多少？"),
    "logistics": ("物流商", "主要用哪几家快递？"),
    "specials": ("特殊业务", "有没有组合装、预售、分销、生产加工这类特殊业务？"),
}


class Requirement(BaseModel):
    """收集到的需求档案。全部可空——多轮对话里是一点一点填起来的。"""

    platforms: str | None = None
    shop_count: str | None = None
    warehouse_mode: str | None = None
    warehouse_count: str | None = None
    daily_orders: str | None = None
    logistics: str | None = None
    specials: str | None = None

    def missing(self) -> list[str]:
        """还没填的字段，**按 REQUIREMENT_FIELDS 的顺序**返回。"""
        return [f for f in REQUIREMENT_FIELDS if not getattr(self, f, None)]

    def filled(self) -> dict[str, str]:
        return {f: v for f in REQUIREMENT_FIELDS if (v := getattr(self, f, None))}

    def human(self) -> str:
        """给模型看的当前状态。已填的列出来，缺的按顺序点名。"""
        lines = [f"{REQUIREMENT_FIELDS[f][0]}：{v}" for f, v in self.filled().items()]
        gaps = self.missing()
        if gaps:
            lines.append("还缺：" + "、".join(REQUIREMENT_FIELDS[f][0] for f in gaps))
        return "\n".join(lines) or "（还什么都没收集到）"


class ChecklistItem(BaseModel):
    """清单里的一行。每一行都要能让人照着在系统里点出来。"""

    area: str = Field(description="所属模块与界面路径，如「设置–基本设置–店铺」")
    item: str = Field(description="要配的这一项叫什么")
    value: str = Field(description="建议怎么配。有具体值就写具体值，别写「按需配置」")
    why: str = Field(description="为什么这么配。**依据要来自知识库检索到的材料**")
    priority: Literal["必做", "建议", "可选"] = "建议"


class Checklist(BaseModel):
    """一份实施配置方案。"""

    title: str = Field(description="方案标题，带上客户的业务特征")
    summary: str = Field(description="两三句话说清这套配置的思路")
    items: list[ChecklistItem] = Field(description="配置项，按实施顺序排")
    open_questions: list[str] = Field(
        default_factory=list,
        description="知识库里没有答案、需要人工确认的问题。**宁可留在这里，也不要编**",
    )
