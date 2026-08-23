"""台账口径：从 `request_trace` 的行算出那几个数（M15-A）。

⚠️ **这个模块存在的唯一理由是「一套口径只写一遍」。**

`copilot quality-report` 和管理台的 Overview 报的是同一批指标。两边各算一次
的话，它们会**慢慢**漂移——不是一次性写错，而是某天有人在其中一处改了
「差评率的分母」或者「延迟算不算寒暄」，另一处没改。表现是两个页面对同一天
给出两个数，而看的人无从判断哪个是真的。

这里的每一条口径都是刻意选的，改动前先读注释：

    差评率的分母  是**被评价过的轮次**，不是全部请求。绝大多数轮没人点过，
                  拿总数当分母只会得到一个恒定接近 0、看不出变化的数
    延迟          **不含寒暄**。寒暄一次模型调用都不花、首字是毫秒级的，
                  混进来会把 p50 拉到看不出问题
    越过工具直答  Agent 路 + 一个工具都没调 + 写出了有出处样子的答案。
                  ⚠️ 只看 Agent 路：直路的 `tools` 恒为空数组，混进来会把
                  每一条直路都算成违规
    tools 为空    **不等于**违规：追问和寒暄都不该调工具。它和上一条分开报，
                  才分得出「Agent 在正常追问」和「Agent 在越线」
    老数据        `answer_source` 是 NULL 的行单独成一类。并进任何一类都会
                  让那一类凭空变大
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注，避免运行时多一次导入
    from copilot.db.models import RequestTrace

# `answer_source` 的取值。和 `copilot.api.trace` 里的常量是同一批，
# 这里不 import 是为了让本模块保持纯函数、不牵进 FastAPI 那一侧
KB = "kb"
GENERAL = "general_knowledge"
CANNED = "canned"
TOOL = "tool"
NO_ANSWER = "no_answer"
VERIFIED = "verified"

# 老数据（M13 P5 之前没有 answer_source 这一列）在报表里的键
LEGACY = "（M13 之前的老数据）"


def pct(num: int, den: int) -> str:
    return f"{100.0 * num / den:.1f}%" if den else "—"


def percentile(values: list[int], q: float) -> int | None:
    """第 q 百分位（q 取 0.5 / 0.95）。

    ⚠️ **自己算，不引 numpy。** 这台机器上装 numpy 是为了一个百分位
    多背 20MB 依赖；而且样本量常常是两位数，这时候「用哪种插值法」
    的差别远小于样本本身的随机性。
    最近邻取法，向下取整：n=20 时 p95 就是第 19 个（0 起数），
    也就是「第二慢的那一次」——对这个量级的数据，这是能给出的最诚实的答案。
    """
    if not values:
        return None
    ordered = sorted(values)
    idx = min(int(q * len(ordered)), len(ordered) - 1)
    return ordered[idx]


@dataclass(slots=True)
class Latency:
    p50: int | None
    p95: int | None
    count: int

    @classmethod
    def of(cls, values: list[int]) -> Latency:
        return cls(p50=percentile(values, 0.5), p95=percentile(values, 0.95), count=len(values))


@dataclass(slots=True)
class Summary:
    """一段时间内的台账概览。**不含任何原始问题文本**。

    ⚠️ 概览页不该展示用户问了什么（路线图第 9.1 节）：一个人问的问题连起来
    就是他在处理什么客户、什么故障。管理员点进详情页 / 单条 trace 时才看得到，
    那是一次明确的动作，不是一眼扫过去的仪表盘。
    `bypass_ids` 只给 id，要看内容得自己去查那一行。
    """

    total: int = 0
    users: int = 0
    by_source: dict[str, int] = field(default_factory=dict)

    up: int = 0
    down: int = 0

    agent_total: int = 0
    agent_no_tool: int = 0
    bypass_ids: list[str] = field(default_factory=list)
    interrupted: int = 0
    errors: int = 0

    ttfb: Latency = field(default_factory=lambda: Latency(None, None, 0))
    duration: Latency = field(default_factory=lambda: Latency(None, None, 0))

    tokens: int = 0
    answered: int = 0  # 不含寒暄的轮次，用来算「平均 token / 回答」

    @property
    def bypass(self) -> int:
        return len(self.bypass_ids)

    @property
    def feedback_rate(self) -> str:
        return pct(self.down, self.up + self.down)


def _is_interrupted(row: RequestTrace) -> bool:
    """用户自己按了停止。

    ⚠️ **不能算成服务出错。** 它在表里和真故障长得一样（`ok=False`），
    但一个是用户改主意了，一个是系统坏了——混在一起看，错误率会随着
    「用户爱不爱按停止」上下浮动，那个数字就再也说明不了任何事。
    """
    return not row.ok and (row.error or "").startswith(("CancelledError:", "GeneratorExit:"))


def summarize(rows: list[RequestTrace]) -> Summary:
    """把一批台账行算成概览。纯函数，不碰数据库。"""
    s = Summary(total=len(rows))
    if not rows:
        return s

    s.users = len({r.user_id for r in rows if r.user_id is not None})

    for r in rows:
        key = r.answer_source or LEGACY
        s.by_source[key] = s.by_source.get(key, 0) + 1

    s.up = sum(r.feedback == "up" for r in rows)
    s.down = sum(r.feedback == "down" for r in rows)

    agent_rows = [r for r in rows if r.route == "agent"]
    s.agent_total = len(agent_rows)
    s.agent_no_tool = sum(not (r.tools or []) for r in agent_rows)
    s.bypass_ids = [
        str(r.id)
        for r in agent_rows
        if not (r.tools or []) and not r.no_answer and r.answer_source == KB
    ]

    interrupted = [r for r in rows if _is_interrupted(r)]
    s.interrupted = len(interrupted)
    s.errors = sum(not r.ok for r in rows) - s.interrupted

    real = [r for r in rows if r.route != "canned"]
    s.ttfb = Latency.of([r.ttfb_ms for r in real if r.ttfb_ms])
    s.duration = Latency.of([r.total_ms for r in real if r.total_ms])

    # ⚠️ **只有一个总数，没有 input / output 的拆分。**
    # `usage.estimate_tokens` 是按字符数估的（连上下文一起算），它压根不区分
    # 进出。要拆就得解析流式响应末尾的 usage 字段——那是另一件事。
    # **宁可少报一个数，也不要报一个编出来的拆分。**
    s.tokens = sum(r.tokens for r in rows)
    s.answered = len(real)
    return s
